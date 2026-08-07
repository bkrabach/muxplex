# Soft Deck Settings Menu — Implementation Specification

Status: **NOT YET BUILT.** Written 2026-08-06 as the specification for
`docs/BACKLOG.md` item 2, "A settings menu inside the soft deck."
Like `2026-08-05-focus-grab-plan.md` and `2026-08-06-settings-recovery-plan.md`,
this is not a record of something that shipped — it is the specification of
record for whenever the work is picked up.

**Backlog item:** `muxplex/docs/BACKLOG.md` §2
**Target repos:** `muxplex` (v0.41.0 + `00e7e84`) — `muxplex-deck` (v0.13.1) **is not modified**
**API changes:** none. Zero new endpoints, zero new fields, zero server settings keys.
**New soft-deck settings keys:** **none.** See §10.
**Depends on:** `docs/plans/2026-08-06-settings-recovery-plan.md` (backlog item 4,
shipped as `00e7e84`). That work exists specifically so this one could ship safely;
§7.0 lists the properties this design inherits from it.

---

## 0. Read this first — most of item 2 has already shipped

**Do not build a settings menu. One already exists, and it already covers almost
everything the backlog entry asks for.**

`muxplex/frontend/deck/index.html:85-174` is a full settings panel with eleven
sections. It is reachable four ways. It configures the grid shape, emulated dials,
an emulated touch strip, session sort, poll interval, brightness, and arbitrary
address→action bindings, and it exports/imports/resets itself. Every "beyond
parity — what only the soft deck can offer" bullet in the backlog entry except one
is already in the tree; that one should not be built (§0.2).

The backlog entry is stale because it was written before the panel was implemented
and never updated. Its four open questions have all been answered in code, with the
reasoning written down in `deck.js`'s own comments. §1 walks the entry line by line
against the source.

**What is actually left is four defects in the existing panel, not a missing
feature.** All four were verified by running the real exported functions, not
inferred (§5). None of them adds a setting. Three of them are the same defect wearing
different clothes: *the panel shows you a configuration, and does not tell you which
parts of it are doing anything.*

### 0.1 What should NOT be built

Collected here so nobody specifies around them. Each is argued in full below.

**0.1.1 Do NOT build a menu system, nested navigation, or tabs.** The panel is one
scrolling column of `<section>` elements and that is the right shape for a
landscape phone. `#deck-settings` is the ONE element on this surface allowed
`overflow-y: auto` (`deck.css:574-583`, and `deck.js:3118-3126` explains why). Adding
navigation *inside* a scrolling form spends interaction budget to hide content that
already fits a scroll. §7 adds exactly two elements and one line of copy.

**0.1.2 Do NOT server-sync any deck setting — including the "synced bindings, local
layout" split the backlog calls "the obvious compromise."** The backlog asks for this
to be scrutinized rather than adopted for being obvious. Here is the scrutiny, and
the answer is no. Three reasons, and the third is new since the entry was written:

1. **`key.N` has no stable meaning across devices.** Bindings are addressed by grid
   position; the grid is viewport-derived (`computeGrid`). `key.7` is a session slot
   on one phone and a reserved control key on another. `deck.js:245-250` already makes
   this argument. §5's F1 makes it worse than the comment claims: `key.N` also has no
   stable meaning across *shapes on the same device* — `reservedControlKeys` flips
   between `corners` and `bottom-row` at `cols === 3`, so rotating a phone can move
   which indices are reserved.
2. **A synced blob is a single write that strands every installed deck at once.**
   `2026-08-06-settings-recovery-plan.md` §0.1 rejected a server-side reset flag partly
   because it is origin-wide. A synced *config* is the same weapon pointed the same
   way, except it fires on every write instead of one. It directly fights the property
   §4 of that document was built to establish.
3. **The backup problem it solves is already solved.** Export/Import
   (`exportSettingsJSON`/`importSettingsJSON`, `index.html:158-169`) covers "lost on
   PWA reinstall, invisible to backup" without taking on `/api/settings`'
   CAS/LWW/federation-sync semantics for a per-screen preference. `deck.js:254-258`.

**0.1.3 Do NOT add `server_url`, `key_file`, or `ca_file` "for CLI parity."** They are
structurally inapplicable, and adding them would create a first-class stranding
vector. The soft deck is served *by* muxplex at `/deck/` and every request is
same-origin and relative (`getJSON('/api/view')`, `deck.js:1906`), authenticated by
the browser's existing muxplex session cookie. There is no URL to configure and no
Bearer key to carry. A `server_url` field could only ever point a working deck at
something that is not muxplex — which is exactly the "points `server_url` at nothing"
failure the backlog's own escape-hatch question names, except it would be a failure
this repo invented on purpose. §2.5.

**0.1.4 Do NOT add a `settings` action to `ACTION_CATALOG`.** Already ruled out by
`2026-08-06-settings-recovery-plan.md` §6.0 and restated here because it is the most
tempting way to answer "let the user choose where Settings lives." `ACTION_CATALOG` is
a byte-for-byte mirror of `muxplex-deck`'s `controls.py` ACTIONS dict, pinned by two
tests: `test_deck.mjs`'s "ACTION_CATALOG mirrors muxplex-deck controls.py's 19-action
catalog exactly (name + kind)" and "ACTION_CATALOG is untouched by the strip feature
— still exactly the mirrored 19 actions" (`test_deck.mjs:954`, `:1123`). Adding a
20th entry breaks both. `STRIP_ACTION_CATALOG` (`deck.js:341`) is the established
escape hatch for a soft-deck-only action; this design needs neither.

**0.1.5 Do NOT add any new gesture.** `2026-08-06-settings-recovery-plan.md` §0.3
closed this: a pointer-timer gesture has no node in the accessibility tree, TalkBack
cannot surface it, and a gesture-only entry point caused the 2026-07 incident. Still
closed.

**0.1.6 Do NOT add long-press as a second binding per key.** The backlog lists it as
"doubles the action surface without doubling the grid." It cannot be built here:
long-press is already the settings accelerator (`deck.js:2053-2103`, armed when
`dataset.role === 'view'`). A per-key long-press binding either collides with that on
the VIEW key or, on every other key, re-creates precisely the accessibility-invisible
affordance §0.1.5 forbids — this time as a *primary* way to invoke arbitrary actions.
If a future item wants a second binding per key, the honest vehicle is a second
*visible* control (another dial, another strip zone), which the panel can already add.

**0.1.7 Do NOT add a theme setting.** The deck's palette is one design system
deliberately ported from the hardware's fixed key faces. `KEY_DESIGN_SYSTEM.md` is the
most-cited missing document in this repo (`BACKLOG.md` §6: 48 refs across 7 files,
including `deck.css` and `deck.js`). Forking a design system that is not written down
is how the second theme becomes permanently half-correct.

**0.1.8 Do NOT add a confirmation to "Reset to defaults."** This one is a reversal of
an instinct, and it deserves its own line because the instinct is a good one. Reset is
a single tap, destructive, irreversible, red, and `00e7e84`'s boot detector now *opens
this panel automatically* for a stranded user — a panicky tap loses every binding they
authored. But Reset is **itself an escape hatch**:
`2026-08-06-settings-recovery-plan.md` §6.3 names it as one of the two ways out of a
stranded state, and the recovery banner's own copy tells the user to use it. Adding
friction to an emergency control to protect against its accidental use is the wrong
trade. The mitigation that already exists is Export, two sections above it. §5's F5.

### 0.2 What is left — and it is small

| # | Finding | Kind | Verified |
|---|---|---|---|
| F1 | A binding that does nothing looks identical to one that works — four distinct ways | defect | §5.1, by execution |
| F2 | Nothing tells the user which key is `key.N` | gap | §5.2, by reading the form |
| F3 | A binding write can strand the user, and unlike a grid write it is never checked | defect | §5.3, by execution |
| F4 | `focus_app` is an offerable, bindable no-op | defect | §5.4, `deck.js:2593` |
| F5 | Reset has no confirmation | **rejected** — see §0.1.8 | §5.5 |

F1, F3 and F4 are all consequences of the settings menu itself: before bindings
existed, none of these states was reachable. F2 is the concrete content of the
backlog's "there is no comfortable way to run `muxplex-deck controls set key.11
view_prev` from it" — the soft deck can accept the text, but unlike the CLI it never
shows you the table that makes the text meaningful.

---

## 1. Establishing what exists — item 2's asks, against the code

### 1.1 The panel, and its four entry points

`#deck-settings` (`deck/index.html:85-174`) is opened by exactly four paths. All four
are live today:

| Path | Where | Accessibility-tree node? | Cost in key slots |
|---|---|---|---|
| SETTINGS key on the view picker | `computeKeyPlan`'s picker branch, `deck.js:1581-1600` | yes (a real key face) | **zero permanent** — the slot only exists while the picker is open |
| Long-press the VIEW key (600ms) | `deck.js:2053-2103` | no (accelerator only, by design) | zero |
| `?settings=1` / `?reset=1` | `checkURLEscapeHatch`, `deck.js:3480-3493` | n/a (URL) | zero — **requires an address bar; unavailable to an installed deck** |
| SETTINGS button on the takeover | `index.html:75-78`, wired `deck.js` | yes | zero — the takeover replaces the grid |

The fourth arrived with `00e7e84`. It is the one affordance no grid shape, no binding,
and no brightness value can remove.

Panel sections today: Session order (`sort`), Polling (`pollIntervalMs`), Grid layout
(`gridOverride` + Auto + inline error), Dials (`dialCount`), Touch strip
(`stripCount`), Brightness (session-local), Key/dial/strip bindings (list + add form +
inline error), Export, Import, Reset. Plus the recovery banner (`role="alert"`) from
`00e7e84`.

### 1.2 Item 2's parity surface, resolved

The entry names `muxplex-deck`'s config keys and says `RELOADABLE_KEYS` is "a strong
hint about which settings are cheap to expose first."

| `muxplex-deck` key | Reloadable? | Soft deck status |
|---|---|---|
| `controls` | yes | **shipped** — `bindings`, with a superset address grammar (§3.2) |
| `sort` | yes | **shipped** — `sort`, same `attention`/`server` vocabulary |
| `poll_interval` | yes | **shipped** — `pollIntervalMs` (ms, not s; §3.2) |
| `focus_app` | yes | **catalog action present, dispatch is a no-op** — F4 (§5.4). The *setting* (a window-title match string) is inherently machine-specific and correctly stays out; `BACKLOG.md` §3 already says so |
| `server_url` | no | **structurally N/A** — same-origin (§0.1.3, §2.5) |
| `key_file` | no | **structurally N/A** — cookie auth |
| `ca_file` | no | **structurally N/A** — the browser's own trust store |

Every reloadable key that *can* apply, applies. Nothing to build.

### 1.3 Item 2's "beyond parity" list, resolved

| Backlog bullet | Status |
|---|---|
| Layout as a setting (rows × cols per device) | **shipped** — `gridOverride`, `computeGridForShape`, and since `00e7e84` a write-time reachability refusal |
| Emulated dials, matching the Stream Deck+ | **shipped** — `dialCount` 0–4, `dial.N.turn`/`dial.N.push`, `dialDragTicks`/`isDialTap` |
| Emulated touch strip | **shipped** — `stripCount` 0–4, `strip.N.tap`/`strip.N.drag`, a live STATUS line, plus `brightness_set` in `STRIP_ACTION_CATALOG` for absolute drag |
| "the relative actions have nowhere to live on a device with no dials" | **resolved** — `view_cycle`/`page_cycle`/`brightness_cycle` are reachable via `dial.N.turn` and `strip.N.drag` (`applyRelativeTicks`, `deck.js:739`) |
| Swipe-along-strip | **shipped** — `strip.swipe.left`/`strip.swipe.right`, whole-strip, no zone index |
| Long-press as a second binding per key | **not shipped, and should not be** — §0.1.6 |
| Haptic feedback on press | **not shipped.** `navigator.vibrate()` is unsupported on iOS Safari, so it would be a setting half the audience cannot have. It cannot strand (no persisted state can suppress a control), so it is safe to add whenever someone wants it — but it is not part of this item |
| Theme | **not shipped, and should not be** — §0.1.7 |
| Brightness for "a phone left on a desk overnight" | **shipped, then deliberately made session-local** by `00e7e84` (`persistableDeckSettings`), matching the hardware. The capability survives; the persistence does not |

Note the design-doc drift this exposes: `DESIGN_SOFTDECK.md` §2 is titled "Dials:
none. Decisively." and argues at length for `dial_count = 0`. That verdict was
superseded by the shipped dial strip. `deck.js:269-273` records the same kind of
reversal for the touch strip ("replaces the earlier 'decorative, not functional'
verdict once the actual `ACTION_CATALOG` was checked"). **This design does not
reconcile those documents** — that is `BACKLOG.md` §6's writing job, not this one's —
but a builder reading `DESIGN_SOFTDECK.md` §2 should know it describes a rejected
past, not the present.

### 1.4 Item 2's four open questions, answered

**"Where does soft-deck config live?"** — `localStorage['muxplex-deck-settings']`,
local-only, with the full argument at `deck.js:237-259` and the synced-split rejection
re-argued at §0.1.2 here.

**"How does it stay honest with `muxplex-deck`?"** — Two golden fixtures, exactly as
the entry prescribes. `deck/layout.fixtures.json` (`spec_version: "1"`, served at
`/deck/layout.fixtures.json`) pins reserved-control-key geometry, paging, and
control-key content; `test_deck.mjs:834-880` asserts `deck.js` against it and
`muxplex-deck`'s suite asserts its own side. `test_deck.mjs:954` pins the 19-action
catalog by name and kind. §3.2 states what this design must not disturb.

**"What does 'subtle and out of the way' actually mean here?"** — Answered, and better
than the entry's own candidate list. The picker SETTINGS key costs **zero permanent
pixels** because the slot exists only while the picker is open (`deck.js:1562-1586`).
That is a real answer to "a settings affordance competes with the keys the user
actually wants," and it beats every candidate the entry floats (gear icon, corner
long-press, edge swipe, bindable action) on the one axis that matters — it is visible,
it is in the accessibility tree, and it is free. §4.

**"What is the escape hatch?"** — `BACKLOG.md` §4, specified in
`2026-08-06-settings-recovery-plan.md`, shipped as `00e7e84`.

**Conclusion.** Every open question is closed. The remaining work (§5) is not what the
entry describes.

---

## 2. Three settings stores, and the rule for routing between them

### 2.1 The stores

| Store | Reach | Out-of-band editor | Can strand? | What belongs there |
|---|---|---|---|---|
| **Server** `~/.config/muxplex/settings.json` | every client of that muxplex, plus federation peers | `muxplex config` + the web UI's address bar | **No** | Anything about the *sessions and the server*: `views`, `hidden_sessions`, `session_commands`, the `input_*` fences, TLS, `device_name`. Shared truth, and the only store with a fence model |
| **Hardware deck** `~/.config/muxplex-deck/config.json` | one physical Stream Deck | `muxplex-deck config` / `muxplex-deck controls`, always available | **No** | *Reaching* the server (`server_url`, `key_file`, `ca_file`) and per-device controls |
| **Soft deck** `localStorage['muxplex-deck-settings']` | one browser profile on one device | **none** | **YES** | Per-screen *presentation* only: grid shape, emulated control counts, bindings, sort, poll interval, session-local brightness |

The first two are safe for the same reason: a second, unaffected tool can always reach
them. The third has exactly one reader and one writer, and the writer is the page the
settings can break. That asymmetry is the whole content of
`2026-08-06-settings-recovery-plan.md` §1, and it is the reason this design adds no
settings at all.

### 2.2 The routing rule, stated once

> **A setting belongs in the store whose reach matches the setting's scope. The soft
> deck's store is the only one that can strand — so every soft-deck setting must be
> justified twice: as genuinely per-screen, AND against "can this strand the user, and
> what gets them back?"**

Applied to the three surfaces:

- **Scope is "these sessions" → server.** Views, hidden sessions, command pairs,
  fences. A soft deck that stored its own view list would immediately disagree with
  the web UI about what a view is.
- **Scope is "this physical device" → that device's local store.** Grid shape is the
  canonical example and the backlog is right that it is inherently per-device.
- **Scope is "how to reach the server" → the store on the machine that has to reach
  it.** For the soft deck this category is empty (§2.5).

### 2.3 What #4's incident actually teaches — it is two questions, not one

`brightness` was in the **right store** (it is per-screen) and still produced the worst
failure in the item-4 writeup: a self-sealing 10% dim whose only exit was a control the
dim itself made illegible. The store was correct; the **lifetime** was not.

So the rule above has a second half a builder must not drop:

> **A per-screen setting that can degrade the surface must also justify *surviving a
> restart*.** If it cannot, it is session-local — the hardware sidecar's answer
> (`muxplex-deck/src/muxplex_deck/main.py:490-497`), and now the soft deck's.

This is the test that catches the class of bug a "which store?" question alone misses.
Any future soft-deck setting gets both questions, not one.

### 2.4 What must never move between stores

- **Nothing from the soft deck moves to the server.** §0.1.2.
- **Nothing from the server reaches into the soft deck.** `muxplex config` cannot touch
  `localStorage`, and a server flag that told decks to wipe themselves would be a
  griefing primitive fronted by the federation Bearer key. Both argued in
  `2026-08-06-settings-recovery-plan.md` §0.1/§0.2. Restated because "just add a CLI
  command" remains the obvious-looking answer that cannot work.
- **The `input_*` and template fences stay `LOCAL_ONLY_KEYS`, untouched.** This design
  writes no server settings at all, so the fence keeps its stated scope: keys naming a
  command or a filesystem path the server executes or reads (`AGENTS.md`).

### 2.5 Why the soft deck's "connection" category is empty

`deck.js` issues only relative, same-origin requests: `/api/view`, `/api/sessions`,
`/api/settings`, `/api/state`, `/api/sessions/{name}/connect` (`deck.js:1906-1907`,
`:2501`, `:2528`, `:3051`). The page is served from `/deck/` by the same muxplex, with
`scope: "/deck/"` in the manifest. Auth is the browser's existing muxplex session
cookie — the shared middleware, no Bearer key, no CA file.

Consequently:

- There is no `server_url` to set. The origin is where the page came from.
- There is no `key_file`. The cookie is the credential.
- There is no `ca_file`. The browser already validated the certificate to load the
  page at all; if it had not, there would be no deck to configure.

Adding any of the three would be adding a field whose only reachable effect is to break
a working deck — and to break it in the one store that has no second editor. **This is
the clearest example in the whole item of "a setting in the wrong store," and the wrong
store here is *any* store.**

---

## 3. The parity question

### 3.1 Why a settings menu on one surface is correct, not a gap

The backlog entry's own justification stands and needs no plan for the other two
surfaces:

- **The hardware deck should not get one.** It has no text input. Its config lives in
  a JSON file driven by a CLI, and `BACKLOG.md` §2 calls that "the right answer *for
  the hardware*." Nothing here changes that. `muxplex-deck` is not modified.
- **The web UI should not get one.** It cannot reach the soft deck's store (§2.4), and
  a server-side flag that could is rejected. There is nothing for it to display that
  would be true.

The divergence is deliberate and one-directional: the soft deck gains a *surface* the
others don't have, for a *store* the others don't share. It gains no vocabulary they
don't share — which is the part that actually has to stay honest.

### 3.2 The standing parity obligations — what must not drift

These are properties of the shared vocabulary, and this design touches none of them.
A builder must verify each still holds before merging.

| Obligation | Pinned by | This design |
|---|---|---|
| `ACTION_CATALOG` is exactly `muxplex-deck`'s 19 actions, same names, same kinds | `test_deck.mjs:954`, `:1123` | **untouched** |
| Soft-deck-only actions live in a second table | `STRIP_ACTION_CATALOG` (`deck.js:341`), `test_deck.mjs:1128` | **untouched** — no new action |
| `key.N` / `dial.N.turn` / `dial.N.push` grammar is identical to `controls.py::parse_address` | `parseControlAddress` (`deck.js:377`), `test_deck.mjs` | **untouched** — no new address form |
| `strip.*` is a forward-compatible *superset*, never a divergence | `deck.js:355-373`'s docstring | **untouched** |
| Reserved-control-key geometry, paging, and control-key content match the shared golden fixture | `deck/layout.fixtures.json` + `test_deck.mjs:834-880` | **read from, never redefined** — §7.1 calls `reservedControlKeys` rather than reimplementing the rule |
| `poll_interval` (seconds, hardware) vs `pollIntervalMs` (milliseconds, soft) | — | **untouched.** These deliberately differ: they are separate stores with separate names, never synced, so there is no unit to reconcile. Do not "unify" them |

### 3.3 One place this design *converges* with the hardware

`muxplex-deck controls` (`cli.py:391`) prints the resolved binding table and reports
bindings that cannot apply to the connected deck — `plan.unapplied`, each with a reason
and a copy-pasteable `muxplex-deck controls unset <address>` remediation. The soft deck
has the same condition (F1) and reports nothing.

§7 adopts the hardware's word for it — **"unapplied"** — for the same idea, so the two
surfaces stay legible to one reader. That is convergence on vocabulary while diverging
on medium, which is the correct shape for these two clients.

---

## 4. Discoverability without clutter — already answered; do not reopen

The constraint is real: a deck is a grid of keys and a settings affordance competes
with the keys the user wants. The shipped answer wins on every axis:

- **Zero permanent pixels.** The SETTINGS key occupies `slots[0]` **only while the view
  picker is open** (`deck.js:1581-1586`). In grid mode — which is ~100% of the time —
  it does not exist.
- **Two guards keep it from making things worse** (`deck.js:1571-1580`): it is never
  placed on a degenerate grid (no controls fit at all, so say so rather than paper
  over it), and never when it would leave zero slots for actual view options.
- **It is in the accessibility tree,** which the 600ms long-press it replaced was not.
  That is the 2026-07 incident's actual lesson.
- **It is never on the page picker** — no reason to duplicate an entry point.

**This design adds nothing to the deck surface.** F2's key map lives *inside* the
panel, where it costs no grid real estate at all. If a future change is tempted to put
anything else on the grid for settings' sake, the answer is the takeover button and the
picker key, which already cover the two cases (surface broken / surface working).

---

## 5. The findings, with reproductions

All reproductions were run against the real exports at `muxplex/frontend/deck/`.

### 5.1 F1 — an inert binding is indistinguishable from a working one

`sanitizeBindings` (`deck.js:482`) validates **grammar and catalog membership only**.
It has no idea what the current grid, dial count, or strip count is — correctly, since
it also runs on every load and must not silently repair a user's stored value. But
`renderBindingsList` (`deck.js:3248`) then prints `addr → action` with nothing else, so
a binding that does nothing renders identically to one that works.

Four distinct mechanisms, all verified:

```
$ cd muxplex/frontend/deck && node -e "…"
sanitizeBindings keeps all four : {"key.20":"refresh_now","dial.2.turn":"view_cycle",
                                   "strip.3.tap":"page_next","key.1":"page_next"}
keyBindingsFromConfig(count=12) : {"1":"page_next"}          <- key.20 silently dropped
dialBindingsFromConfig(dials=0) : []                          <- dial.2.turn silently dropped
stripZoneBindings…(zones=0)     : []                          <- strip.3.tap silently dropped
```

**A. Out-of-range `key.N`.** `keyBindingsFromConfig` (`deck.js:504`) skips
`a.index >= keyCount`. A `key.20` binding on a 12-key grid is stored, listed, and inert.
Reachable by shrinking the grid after binding, or by rotating the device under an auto
grid.

**B. `dial.N.*` with `dialCount === 0`** (the default), or `N >= dialCount`.
`dialBindingsFromConfig` (`deck.js:524`) only fills `0..dialCount-1`.

**C. `strip.N.*` with `stripCount === 0`** (the default), or `N >= stripCount`.
`stripZoneBindingsFromConfig` (`deck.js:545`), same shape. **`strip.swipe.left/right`
is a distinct sub-case**: `stripSwipeBindingsFromConfig` (`deck.js:566`) takes no count
and resolves the binding regardless — but `#deck-touch-strip` carries `.hidden` when
`stripCount === 0`, and the swipe listener is on that element (`wireTouchStrip`,
`deck.js:2977`). So the binding resolves and the gesture is unreachable:

```
stripSwipeBindingsFromConfig(sanitize({'strip.swipe.left':'page_next'}))
  -> {"left":"page_next","right":"none"}     // resolves even at stripCount 0
```

**D. `key.N` on a reserved control key — the worst of the four**, because it looks
most like it should work:

```
reserved 3x2  : {"mode":"corners","view":0,"prev":4,"next":5}
boundKeys     : {"0":"refresh_now"}          // key.0 == reserved VIEW
plan[0] role  : view   | name: "VIEW"        // the binding was painted, then overwritten
slots(bound)  : [1,2,3]
slots(unbound): [1,2,3]                      // costs no extra slot, so nothing else hints
```

`computeKeyPlan` paints bound faces first (`deck.js:1537-1553`), then
`_setControlFace` overwrites `reserved.view/prev/next` (`deck.js:1651-1653`). This is
correct and load-bearing — it is *why* VIEW cannot be bound away, which
`2026-08-06-settings-recovery-plan.md` §2.3 D relies on. The cost is that the user's
binding vanishes with no trace anywhere except the rendered face they were not looking
at.

**Why this is the highest-value finding.** It is the "indistinguishable divergence" bug
class `DECK_PARITY_ARCHITECTURE.md` is cited for throughout this codebase, self-inflicted
by the settings UI, on the one surface with no second tool to check against. And the
hardware already solved it (§3.3).

### 5.2 F2 — nothing tells the user which key is `key.N`

`#settings-add-address` is a free-text input with placeholder `key.3`
(`index.html:151`). The only guidance is a `<p>` listing the address *forms*
(`index.html:148`). To bind the top-left key on a 3×4 grid the user must know:

- indices are row-major from 0 (never stated anywhere in the UI),
- which indices are reserved — and that this **moves with the grid shape**:
  `reservedControlKeys` returns `corners` (VIEW=0, PREV=`(rows-1)*cols`,
  NEXT=`rows*cols-1`) normally, but `bottom-row` (PREV/VIEW/NEXT left-to-right on the
  last row) when `cols === 3 && rows >= 2` (`deck.js:1119-1135`),
- and, given F1-D, that binding a reserved index does nothing.

The hardware's answer to the same question is `muxplex-deck controls`, which prints the
resolved table. **The soft deck has a screen and currently does worse than a terminal.**
That is the concrete content of the backlog's "no comfortable way to run
`muxplex-deck controls set key.11 view_prev` from it."

### 5.3 F3 — a binding write can strand, and is never checked at write time

Verified against `settingsReachability`:

```
3x2 bare            : {"level":"full","reasons":[]}
3x2 + key.1 bound   : {"level":"full","reasons":[]}
3x2 + key.1, key.2  : {"level":"longpress-only","reasons":["bindings-consumed-slots"]}
gridOverrideReachability(3,2) : {"ok":true,"reason":""}   // the SHAPE is fine
```

`sessionSlotIndices` excludes bound indices from the pool, and the picker only places
the SETTINGS key when `slots.length >= 2` (`deck.js:1583`). Two bindings on a 3×2 grid
drop it to 1 and the SETTINGS key disappears — leaving only the
accessibility-invisible long-press, which `2026-08-06-settings-recovery-plan.md` §3
defines as a **failure, not a degraded pass**.

`00e7e84` catches this **at the next cold start**, via the boot detector, with reason
`bindings-consumed-slots` and copy that says "remove a binding below to free a slot."
That is correct and must stay. But between the write and that restart the user has no
signal at all, and the write that caused it is one tap in a panel that is open right now.

**This is an asymmetry with the doctrine `00e7e84` established**, not a new policy
question. That document refuses stranding *grid shapes* at write time (§6.2, decidable
from `(rows, cols)`) and deliberately declines to refuse *bindings* (§5.4: "not worth a
write-time refusal that would have to be re-litigated on every resize"). Both decisions
are right. The gap is that declining to *refuse* was silently taken as declining to
*say anything* — and the same document already established the third option, in the
import handler: **warn, apply, do not repair** (§6.2, `deck.js:3449-3464`).

### 5.4 F4 — `focus_app` is an offerable, bindable no-op

`deck.js:2593-2601`:

```js
case 'focus_app':
  // Not yet implemented -- BACKLOG.md item 3 …
  if (typeof console !== 'undefined' && console.info) {
    console.info('muxplex deck: focus_app is not yet implemented (see BACKLOG.md item 3)');
  }
  return;
```

`focus_app` is `MOMENTARY` in the shared catalog, so `validActionsForAddress` offers it
for every `key.N`, every `dial.N.push`, every `strip.N.tap`, and both swipe directions.
A user binds it, gets a key face reading `FOCUS / APP` (`ACTION_CATALOG` label,
`deck.js:316`), presses it, and the only evidence of failure is a `console.info` no
phone user will ever see.

The comment calls this "a documented no-op rather than a silent dead binding." That is
true of the *code*; from the user's seat it is a silent dead binding. And note the
provenance: before the settings menu existed, `focus_app` was unreachable on the soft
deck and this was harmless. **The settings menu is what turned a dormant catalog entry
into a user-visible dead control.** The fix belongs to this item even though the
capability belongs to item 3.

### 5.5 F5 — Reset has no confirmation: considered, and rejected

Stated in full at §0.1.8. Recorded here so a future reader does not re-derive it as an
oversight. The risk is real (one tap, irreversible, and `00e7e84` auto-opens the panel);
the fix is wrong, because Reset is one of the two documented exits from a stranded
state and friction on an emergency control is a worse trade than the loss it prevents.
Export, two sections above it, is the mitigation that exists.

**If a builder wants to reduce the risk at zero interaction cost**, the honest lever is
copy, not a gate: the recovery banner could mention copying the Export blob before
resetting. That is optional, is not specified below, and must not become a modal.

---

## 6. The design, in one paragraph

Tell the user what their configuration actually does on this device: classify every
binding against the current grid/dial/strip shape and mark the ones that are inert
(with the reason, using the hardware's word for it); draw a read-only map of the key
grid inside the panel so `key.N` stops being a number the user has to guess, and let
tapping a cell fill the address field; warn — never refuse — when the binding set has
consumed the SETTINGS key's slot; and route `focus_app` through the same
inert-binding machinery so the one dead action in the catalog says so. Three new pure
functions, two new elements, one changed render path, no new setting, no new action, no
API surface, and nothing added to the deck's grid.

---

## 7. Implementation specification

All paths relative to `muxplex/`. No Python changes. No `/api/*` changes. No
`muxplex-deck` changes.

### 7.0 Constraints this design honors — verify each before merging

- **No new soft-deck setting.** `defaultDeckSettings()` is byte-identical after this
  change. This discharges `2026-08-06-settings-recovery-plan.md` §10's standing rule
  vacuously — see §10.
- **`ACTION_CATALOG` untouched.** No 20th action. `test_deck.mjs:954` and `:1123` must
  pass unmodified.
- **`STRIP_ACTION_CATALOG` untouched.** Still exactly one CONTINUOUS action.
- **`parseControlAddress` grammar untouched.** No new address form.
- **`layout.fixtures.json` untouched.** §7.1 *calls* `reservedControlKeys`; it must
  never reimplement the corners/bottom-row rule.
- **`/api/*` gains nothing.** Additivity satisfied vacuously.
- **`LOCAL_ONLY_KEYS` untouched.** Nothing here writes server settings.
- **`mergeDeckSettings` and `sanitizeBindings` keep their recovery posture.** Both stay
  pure, both keep dropping bad *fields* rather than rejecting blobs, and neither gains
  device-shape awareness. Applicability is a *read-time* classification, deliberately
  not a write-time filter — the same reasoning `2026-08-06-settings-recovery-plan.md`
  §5.3 uses for `tooSmall`: the device shape is not stable, so a value that is inert
  today may be correct after a rotation, and repairing it would destroy a choice the
  user made.
- **The four Settings entry points are unchanged**, including `checkURLEscapeHatch`.
- **`persistableDeckSettings`' brightness exclusion is unchanged.**
- **`#deck-surface` is `display: contents`** (`deck.css:78`) and generates no box. This
  design applies no filter or layout property there. It is named here because it is the
  trap `00e7e84` hit; §7.3's key map is a **new element inside `#deck-settings`**, which
  is a real box (`position: fixed; inset: 0`).

### 7.1 Change 1 — `bindingApplicability`: classify every binding against the device

**`deck.js`, pure-logic section**, next to `sanitizeBindings`. New exported function:

```js
/**
 * Classify every configured binding against the CURRENT device shape --
 * which of them actually do something here, and why the rest don't.
 *
 * Deliberately a READ-TIME classification, never a write-time filter:
 * `sanitizeBindings` validates grammar and catalog membership only, and
 * must keep doing exactly that (a binding that is inert on today's grid
 * may be correct after a rotation or a dialCount change, and silently
 * repairing it would destroy a choice the user made -- same reasoning
 * docs/plans/2026-08-06-settings-recovery-plan.md §5.3 applies to
 * `tooSmall`).
 *
 * "unapplied" is muxplex-deck's own word for this condition
 * (cli.py::controls_show / layout.plan_layout's `plan.unapplied`) --
 * deliberately reused so the two surfaces stay legible to one reader.
 *
 * Reserved-key detection calls `reservedControlKeys` rather than
 * reimplementing the corners/bottom-row rule; that rule is pinned by
 * deck/layout.fixtures.json and must have exactly one implementation.
 *
 * @param {Object<string,string>} bindings   sanitized address -> action
 * @param {{rows:number, cols:number, dialCount:number, stripCount:number}} shape
 * @returns {Array<{address:string, action:string, applies:boolean, reason:string}>}
 *          ascending by address (same order renderBindingsList already uses)
 */
function bindingApplicability(bindings, shape) { /* ... */ }
```

**Reason codes** — stable strings; the DOM layer maps them to copy:

| Code | Emitted when |
|---|---|
| `''` | the binding applies |
| `key-out-of-range` | `key.N` and `N >= rows*cols` |
| `key-is-reserved-control` | `key.N` and N is `reserved.view`/`prev`/`next` (F1-D) |
| `no-dials` | any `dial.*` and `dialCount === 0` |
| `dial-out-of-range` | `dial.N.*`, `dialCount > 0`, `N >= dialCount` |
| `no-strip` | any `strip.*` — zone **or** swipe — and `stripCount === 0` |
| `strip-zone-out-of-range` | `strip.N.*`, `stripCount > 0`, `N >= stripCount` |
| `unsupported-on-soft-deck` | action is `focus_app` (§7.5) |

**Evaluation order is address-level first, then action-level.** A `key.20 → focus_app`
binding on a 12-key grid reports `key-out-of-range`, not `unsupported-on-soft-deck`,
because the address problem is the more actionable fix and it is the one that will
*still* be true after backlog item 3 lands.

`strip.swipe.left`/`right` have `index === null`; they get `no-strip` when
`stripCount === 0` (F1-C's sub-case) and apply otherwise. Do not give them a zone
range check — there is exactly one strip.

Pure, DOM-free, testable in the same style as every other function in this section.

### 7.2 Change 2 — the bindings list reports applicability

**`deck.js`, `renderBindingsList()` (`:3248`).** Replace the `Object.keys(...).sort()`
loop's data source with `bindingApplicability(deckSettings.bindings, shapeNow())` (see
§7.4 for `shapeNow`). For each entry:

- Keep the existing `addr → action` label and Remove button unchanged.
- When `applies === false`, add the literal class `settings-binding-row--unapplied` via
  `classList.add` and append a `<span class="settings-binding-reason">` carrying the
  copy for the reason code.

**Copy, keyed by reason code.** Each names the condition *and* the fix, matching the
established tone of `#settings-grid-error` and the recovery banner:

| Code | Copy |
|---|---|
| `key-out-of-range` | `no key N on this <R>×<C> grid — use a larger grid, or an address below key.<R*C>` |
| `key-is-reserved-control` | `key <N> is the <VIEW\|PREV\|NEXT> control on this grid — that face always wins, so this binding never shows` |
| `no-dials` | `no dials configured — set Dial count above 0` |
| `dial-out-of-range` | `only <D> dial(s) configured — use dial.0…dial.<D-1>` |
| `no-strip` | `no touch strip configured — set Strip zone count above 0` |
| `strip-zone-out-of-range` | `only <Z> strip zone(s) configured — use strip.0…strip.<Z-1>` |
| `unsupported-on-soft-deck` | `focus_app is not supported on the soft deck yet (see BACKLOG.md item 3) — it will do nothing` |

**`role="alert"` is deliberately NOT used here.** These are steady-state annotations on
a list, not an interruption; the recovery banner owns the one alert role on this panel.

**Do not remove, refuse, or repair an unapplied binding.** It is reported and kept.
A user who bound `dial.0.turn` before setting Dial count is one field away from making
it work, and deleting it for them would be the repair `mergeDeckSettings`' documented
posture forbids.

### 7.3 Change 3 — a read-only key map inside the panel

**`deck/index.html`**, inside the "Key/dial/strip bindings" `<section>`, immediately
after the existing address-form `<p>` and before `#settings-bindings-list`:

```html
<p>Tap a key below to fill the address field.</p>
<div id="settings-key-map" class="settings-key-map" role="list" aria-label="key addresses"></div>
```

**`deck.js`, new function `renderKeyMap()`** in the settings-panel section. Reads the
same primitives the real grid does — never its own copy of any rule:

- shape from `shapeNow()` (§7.4);
- `reservedControlKeys(rows, cols)` for VIEW/PREV/NEXT;
- `keyBindingsFromConfig(deckSettings.bindings, rows*cols)` for bound faces.

One cell per index `0..rows*cols-1`, laid out with
`style.gridTemplateColumns = 'repeat(' + cols + ', 1fr)'` so it is spatially faithful.
Each cell shows the index and a short role tag:

| Cell state | Tag | Class (applied via `classList.add`, string literal) |
|---|---|---|
| `reserved.view` | `VIEW` | `settings-key-map-cell settings-key-map-cell--reserved` |
| `reserved.prev` | `PREV` | same |
| `reserved.next` | `NEXT` | same |
| in `boundKeys` | the action name | `settings-key-map-cell settings-key-map-cell--bound` |
| otherwise | `·` | `settings-key-map-cell` |

Tapping a cell sets `#settings-add-address`'s value to `key.<index>` and dispatches the
same `input` event the field's own listener uses, so the action dropdown refreshes
through the existing `refreshActionOptions` path — no second code path.

**When `grid.tooSmall` or `reserved.mode === 'degenerate'`**, render the cells anyway
(the shape is still what it is) and add one line of copy above the map naming the
condition. The user is very likely here *because* of that shape, and an empty box would
tell them nothing.

**Deliberately no dial map and no strip map.** Their addresses are `dial.0…3` /
`strip.0…3` in an obvious left-to-right row, the count is in a field directly above,
and there is no spatial question to answer. A one-`<p>` hint was also considered for
the keys and rejected: it cannot express that the reserved positions *move* between
`corners` and `bottom-row` mode, which is the part that is actually non-obvious (§5.2).

**Deliberately read-only.** This is a legend, not an editor. Do not grow it into
drag-and-drop binding assignment: that is a second, richer input path for the same
state, on a surface whose whole discipline is one way to do each thing.

**Trap, stated because the constraints name it:** the three cell classes are applied by
JS, so `test_css_class_definitions.mjs` **will** scan them and **will** fail if they are
not defined in `deck.css`. That is the desired behavior. Apply them with
`classList.add('settings-key-map-cell')`-style **string literals**, not assembled in a
template literal — a template literal would make them invisible to the test (the
compose-bar precedent) and forfeit the guard for no benefit.

**`deck.css`** gains `.settings-key-map`, `.settings-key-map-cell`,
`.settings-key-map-cell--reserved`, `.settings-key-map-cell--bound` — `display: grid`,
`gap: 4px`, `aspect-ratio: 1`, ~10px monospace label, borrowing
`#deck-settings input`'s existing `#101018` / `#2A2A4A` palette so it reads as part of
the form. It must stay small enough that a 32-cell map fits above the fold of a
scrolling panel on a ~390px-tall landscape phone — cap the whole map at ~30% of the
viewport height and let it shrink cells rather than overflow.

### 7.4 Change 4 — warn when the binding set has consumed the SETTINGS slot

**`deck.js`, DOM section — one shared shape source.** `renderBindingsList` and
`renderKeyMap` both need the grid the deck will *actually* use, and `recomputeGrid()`
cannot be called while the panel is open (it rebuilds the grid DOM, toggles
`.too-small` on `#deck-root`, and rebuilds the strips — all side effects under a panel
that covers the surface). Extract the shape computation `recomputeGrid` already does
into one helper both call:

```js
/**
 * The grid the deck will use for the CURRENT settings, computed without
 * any of recomputeGrid()'s DOM rebuilding -- safe to call while the
 * Settings panel is open.
 *
 * applyStripOffsets() MUST run first: it writes --reserved-bottom onto
 * #deck-root, whose padding contributes to root.clientHeight, which
 * contentBox() reads. Calling this after a dialCount/stripCount change
 * without it measures the OLD padding and returns a stale shape.
 */
function effectiveGridNow() {
  applyStripOffsets();
  var box = contentBoxForDials(contentBoxForStrip(contentBox(), deckSettings.stripCount),
                               deckSettings.dialCount);
  return computeEffectiveGrid(box.w, box.h, deckSettings.gridOverride);
}

/** The shape triple bindingApplicability/renderKeyMap take. */
function shapeNow() {
  var g = effectiveGridNow();
  return { rows: g.rows, cols: g.cols,
           dialCount: deckSettings.dialCount, stripCount: deckSettings.stripCount,
           tooSmall: !!g.tooSmall };
}
```

`recomputeGrid()` (`:2189`) is rewritten to call `effectiveGridNow()` for its `g`,
dropping its own `applyStripOffsets()` and content-box lines. **One composition site,
two readers, no drift** — this is why it is an extraction and not a second copy.
`#deck-root` is not hidden when the panel opens (only `#deck-surface` is), so the
measurement is valid.

**The warning.** `renderBindingsList()` owns it end to end — recomputed on every render,
so it can never go stale after an add *or* a remove, and needs no event wiring:

```js
var s = shapeNow();
var reach = settingsReachability({
  rows: s.rows, cols: s.cols, tooSmall: s.tooSmall,
  boundKeys: keyBindingsFromConfig(deckSettings.bindings, s.rows * s.cols),
  gridOverride: deckSettings.gridOverride,
});
// level !== 'full' -> populate #settings-bindings-warning; otherwise clear it
```

**`deck/index.html`**, at the end of the bindings `<section>`, after
`#settings-add-error`:

```html
<p id="settings-bindings-warning" class="settings-error"></p>
```

Copy for `bindings-consumed-slots`:

> `Your bindings have filled every open slot, so the SETTINGS key has nowhere to go on the picker — after you leave this panel, only the invisible long-press would get you back. Remove a binding above, or use a larger grid.`

For the other reason codes (`grid-too-small`, `grid-degenerate`,
`grid-too-few-keys`) the grid is the culprit, not the bindings —
`#settings-grid-error` and the boot banner already speak to those, so
`#settings-bindings-warning` stays empty for them. Only `bindings-consumed-slots`
populates it.

**A warning, never a refusal.** Three reasons, in order of weight: (1) the condition is
viewport-dependent and would have to be re-litigated on every rotation — the argument
`2026-08-06-settings-recovery-plan.md` §5.4 already made for declining a refusal here;
(2) refusing would make the panel disagree with its own import handler, which warns and
applies for the structurally identical case (`deck.js:3449-3464`); (3) the boot detector
already catches it on the next cold start, so the cost of being wrong is bounded and
self-explaining.

**Reuse of `.settings-error` for a warning is deliberate and has precedent** —
`#settings-import-error` carries `00e7e84`'s import warning through the same class.
Do not introduce a second severity vocabulary for one string.

**New invariant, and the reason it is stated as an invariant:** *every handler that
mutates `gridOverride`, `dialCount`, or `stripCount` must call
`populateSettingsForm()`* — because all three change what applies (§7.1) and what the
map draws (§7.3). Today `applyGridOverride` and the Auto button do; the `dialInput` and
`stripInput` change handlers (`deck.js:3352-3376`) **do not** and must be updated.
`populateSettingsForm()` calls `renderKeyMap()` then `renderBindingsList()`.

### 7.5 Change 5 — `focus_app` rides Change 1's machinery

No new mechanism. `bindingApplicability` returns `unsupported-on-soft-deck` for any
binding whose action is `focus_app`, and §7.2's copy names it.

**Why not hide `focus_app` from the action dropdown instead.** Two reasons. It would
make the two decks' action vocabularies silently differ in the UI, which is the drift
the catalog fixture exists to prevent — the catalog would still contain it, the
dropdown would not, and nothing would pin the discrepancy. And it would need to be
*un*-hidden by whoever lands backlog item 3, by remembering. Marking it is honest now
and self-repairing later: item 3's implementer deletes one branch in
`bindingApplicability` and one row of copy, and the test that pins the reason code
fails until they do.

**The key face is unchanged.** A `FOCUS / APP` face still paints normally. Adding a
"this key is dead" face state would invent vocabulary `DESIGN_SOFTDECK.md` §7 does not
have (its entire vocabulary is the 55%-opacity stale dim and the whole-surface
takeover), for a condition that is temporary by construction. This also converges with
the hardware, where `controls_show` reports unapplied bindings and the deck's own faces
do not.

### 7.6 Exports and cross-file guards

Add to `module.exports` (`:3533`): `bindingApplicability`. It is the only new *pure*
function; `effectiveGridNow`/`shapeNow`/`renderKeyMap` live inside the DOM closure and
are not exported, consistent with every other DOM-bound helper.

`test_deck.mjs`'s "deck.js exports all pure functions" test enumerates the export list
and must be extended in the same commit.

New top-level bindings must not collide with `app.js` / `terminal.js` globals
(`AGENTS.md`, "Frontend classic scripts share one global scope").
`test_shared_scope.mjs` covers this automatically; `bindingApplicability` is unique
today — verify, do not assume.

Every class this change applies via `classList` must exist in `deck.css` or
`test_css_class_definitions.mjs` fails. The five new classes
(`settings-key-map-cell`, `settings-key-map-cell--reserved`,
`settings-key-map-cell--bound`, `settings-binding-row--unapplied`,
`settings-binding-reason`) are all JS-applied string literals and are therefore all in
scope for that test — by choice (§7.3). `.settings-key-map` is applied in HTML and falls
outside the test's scope; define it anyway and note that it does.

---

## 8. Files touched

| File | Change |
|---|---|
| `frontend/deck/deck.js` | `bindingApplicability` (new, pure, exported); `effectiveGridNow`/`shapeNow`/`renderKeyMap` (new, DOM closure, not exported); `recomputeGrid` (rewritten to use `effectiveGridNow`); `renderBindingsList` (applicability markers + the reachability warning); `populateSettingsForm` (calls `renderKeyMap`); `dialInput`/`stripInput` change handlers (call `populateSettingsForm`) |
| `frontend/deck/index.html` | `#settings-key-map` + its one-line hint; `#settings-bindings-warning` |
| `frontend/deck/deck.css` | `.settings-key-map`, `.settings-key-map-cell`, `--reserved`, `--bound`, `.settings-binding-row--unapplied`, `.settings-binding-reason` |
| `frontend/tests/test_deck.mjs` | new table-driven tests (§9) + the exports list |
| `docs/BACKLOG.md` | delete item 2 (it graduates to this file); update the §Notes entries that reference it (item 4's note already points here in spirit) |

**Not touched:** any Python file, any `/api/*` route, `settings.py`,
`docs/API_SEMANTICS.md`, `deck/layout.fixtures.json`, `deck/manifest.json`, `deck/sw.js`,
`muxplex-deck/**`, and `CHANGELOG.md` / version numbers (release-owner territory per
`AGENTS.md`).

**Deliberately not touched, and worth naming:** `deck/DESIGN_SOFTDECK.md` §2 ("Dials:
none. Decisively.") is stale (§1.3). Reconciling the four `DESIGN_*.md` documents is
`BACKLOG.md` §6's job and must not be folded in here — it is a writing project with no
shortcut, and doing it half-way inside a feature PR is how the current drift happened.

---

## 9. Evidence requirements

Each item is pass/fail with a named artifact.

### Unit — `node --test frontend/tests/*.mjs` (use the glob; see `AGENTS.md`)

| # | Assertion |
|---|---|
| U1 | `bindingApplicability` returns `applies: false, reason: 'key-out-of-range'` for `key.20` at `{rows:3,cols:4}`, and `applies: true` for `key.1` in the same call |
| U2 | `key-is-reserved-control` for `key.0` at `{rows:3,cols:2}` (corners) — and `applies: true` for `key.0` at `{rows:2,cols:3}` (bottom-row, where index 0 is NOT reserved). **Both directions**, because this is the case that moves with grid mode |
| U3 | `no-dials` for `dial.0.turn` at `dialCount: 0`; `dial-out-of-range` for `dial.2.push` at `dialCount: 2`; `applies: true` for `dial.1.turn` at `dialCount: 2` |
| U4 | `no-strip` for `strip.0.tap` **and** for `strip.swipe.left` at `stripCount: 0`; `strip-zone-out-of-range` for `strip.3.drag` at `stripCount: 2`; `applies: true` for `strip.swipe.left` at `stripCount: 1` |
| U5 | `unsupported-on-soft-deck` for `key.1 → focus_app` at a shape where `key.1` is valid — and `key-out-of-range` (**not** the action reason) for `key.20 → focus_app` at `{rows:3,cols:4}`, pinning the evaluation order in §7.1 |
| U6 | Output is ascending by address and contains exactly one entry per configured binding — no drops, no duplicates |
| U7 | `bindingApplicability` does not mutate its `bindings` argument (deep-equal before/after) |
| U8 | `sanitizeBindings` is byte-for-byte unchanged in behavior: it still accepts `key.20`, `dial.2.turn`, `strip.3.tap` regardless of shape. This is a **guard against the tempting wrong fix** — filtering at write time |
| U9 | `defaultDeckSettings()` deep-equals its pre-change value (no new setting) |
| U10 | `ACTION_CATALOG` is still exactly 19 entries and `STRIP_ACTION_CATALOG` still exactly 1 — the existing tests, re-run unmodified |
| U11 | exports list in "deck.js exports all pure functions" includes `bindingApplicability` |
| U12 | `test_shared_scope.mjs` and `test_css_class_definitions.mjs` pass unmodified |

### Smoke — real installed PWA on a real phone, one landscape device minimum

`test_deck.mjs` is deliberately DOM-free, so these are the parts no unit test in this
repo can reach. Run against a real muxplex, with the deck **installed to the home
screen**, not in a tab.

| # | Scenario | Pass condition |
|---|---|---|
| S1 | Bind `key.20` on a grid smaller than 21 keys → look at the list | Row is visibly marked and names the `key-out-of-range` reason with the real R×C |
| S2 | Bind `dial.0.turn` with Dial count 0, then set Dial count to 1 **without closing the panel** | Marker disappears the moment the count changes. **Catches the missing `populateSettingsForm()` call in the dial handler (§7.4)** |
| S3 | Set Dial count 1 → Strip zone count 1 → watch the key map | Map re-draws to the new auto grid each time. **Catches a dropped `applyStripOffsets()` in `effectiveGridNow` (§7.4) — the stale-padding trap** |
| S4 | Tap a cell in the key map | Address field fills with `key.<N>`; the action dropdown repopulates without a second tap |
| S5 | On a 3×2 grid, bind two session slots | `#settings-bindings-warning` appears naming the SETTINGS-key slot. Bindings are **still saved** (this is a warning, not a refusal) — verify in the Export blob |
| S6 | From S5, remove one binding | Warning clears immediately, without closing/reopening the panel |
| S7 | Bind `key.0` on a grid in `corners` mode | Row marked `key-is-reserved-control`; the real VIEW key on the grid behind still says `VIEW` and still long-presses |
| S8 | Bind `focus_app` to any key | Row marked `unsupported-on-soft-deck`; the key face still paints `FOCUS / APP` normally (§7.5) |
| S9 | Set a 4×8 grid (32 cells) and open the bindings section | The map fits without pushing the add-binding form off a scroll the user can reach; cells shrink, nothing overflows horizontally |
| S10 | Set a grid the current viewport cannot draw (e.g. 12×2 landscape) → the takeover appears → tap SETTINGS | Recovery banner still fires exactly as `00e7e84` shipped it; the key map renders with its too-small note rather than an empty box (§7.3) |
| S11 | `/deck/?settings=1` and `/deck/?reset=1` in a browser tab | Behave exactly as before this change |

### Regression

`make test` (Python suite, in a DTU — **never** on a host running a live muxplex) must
stay green. It exercises no line of this change; run it to prove that, not to prove the
change works.

`node --test frontend/tests/*.mjs` must be green as a whole, not just `test_deck.mjs` —
the glob is load-bearing (`AGENTS.md`).

---

## 10. Discharging item 4's standing rule

`2026-08-06-settings-recovery-plan.md` §10 left one obligation on this item:

> **any new soft-deck setting must be added to §2.1's table with an explicit
> strands/does-not-strand verdict, and if it can strand, to `settingsReachability`'s
> reason codes.**

**This design adds no settings.** `defaultDeckSettings()` is unchanged (U9 pins it).
The obligation is discharged vacuously, and that is the strongest single piece of
evidence that this design is safe to ship on top of `00e7e84`.

For completeness, the item-4 §2.1 table with this design's additions marked — every row
is a re-statement, none is new:

| Key | Accepted range | Strands? | Mechanism / mitigation |
|---|---|---|---|
| `version` | `1` | no | — |
| `sort` | `attention` \| `server` | no | ordering only |
| `pollIntervalMs` | 500–60000 | no | 60s stale is annoying, never unreachable |
| `gridOverride` | `{rows,cols}` 1–12 each, product ≤ `N_MAX` | **yes** | write-time refusal (`gridOverrideReachability`) + boot detector + takeover SETTINGS button |
| `dialCount` | 0–4 | contributing | shrinks the content box toward `tooSmall`; takeover + detector cover it. **§7.3's key map now shows the effect before the user closes the panel** |
| `stripCount` | 0–4 | contributing | same |
| `brightness` | 10–100 | no (since `00e7e84`) | session-local; never persisted |
| `bindings` | address → action | **yes (partial)** | boot detector (`bindings-consumed-slots`). **§7.4 now also warns at write time — the gap this design closes** |

Two of the eight rows changed, both in the mitigation column, both strictly
strengthening. No row changed its strands verdict.

**The rule this document leaves behind for whoever comes next:** the two questions in
§2.2 and §2.3 — *is this genuinely per-screen?* and *does it deserve to survive a
restart?* — are asked of every future soft-deck setting, and the answer to the second
is written into `persistableDeckSettings` rather than into a comment.

---

## 11. What this deliberately leaves for later

Named so they are decisions rather than omissions:

- **`focus_app` actually working** — `BACKLOG.md` §3 / `docs/plans/2026-08-05-focus-grab-plan.md`.
  When it lands, delete `unsupported-on-soft-deck` from `bindingApplicability` and its
  row of copy; U5 fails until you do.
- **Haptics** (`navigator.vibrate` on press) — genuine but tiny, unsupported on iOS
  Safari, and it cannot strand. Safe whenever someone wants it; not this item (§1.3).
- **Reconciling `DESIGN_SOFTDECK.md` §2 and the other three `DESIGN_*.md` documents**
  with what shipped — `BACKLOG.md` §6, a writing project (§8).
- **`KEY_DESIGN_SYSTEM.md`**, cited 48 times across 7 files including `deck.css` and
  `deck.js` and still absent — `BACKLOG.md` §6. It is a precondition for any future
  work on faces or theming, which is one more reason §0.1.7 says no to themes now.

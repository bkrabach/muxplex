# Settings Recovery — Implementation Specification

Status: **NOT YET BUILT.** Written 2026-08-06 as the specification for
`docs/BACKLOG.md` item 4, "Recovering when Settings itself is unreachable."
Like `2026-08-05-focus-grab-plan.md`, this is not a record of something that
shipped — it is the specification of record for whenever the work is picked up.

**Backlog item:** `muxplex/docs/BACKLOG.md` §4
**Target repos:** `muxplex` (v0.41.0) — `muxplex-deck` (v0.13.1) **is not modified**
**API changes:** none. Zero new endpoints, zero new fields, zero settings keys.

---

## 0. Read this first — what should NOT be built

Four of the directions in the backlog's framing are wrong. They are collected
here so nobody specifies around them; each is argued in full in §5.

### 0.1 Do NOT build the dashboard / server-side "reset this device's deck"

The backlog lists it first among candidate directions and asks (OQ3) whether it
needs new API surface. It does, and it should not be built anyway, for four
independent reasons:

1. **There is no per-device identity to target.** The soft deck has no server-side
   registration. Any device id would have to be minted into `localStorage` — the
   exact store that is broken in every scenario this item exists to fix. A reset
   flag on the server is therefore **origin-wide**: it resets every deck pointed
   at that muxplex, including the healthy ones on other phones.
2. **It is a griefing primitive fronted by the federation Bearer key.** A settings
   key meaning "every deck that loads next wipes itself" is PATCHable by any
   Bearer-key holder — the same credential `docs/AGENT_GUIDE.md` hands to headless
   agents. It names no command and no path, so it would not qualify for
   `LOCAL_ONLY_KEYS` under that fence's stated scope, yet it would be remotely
   triggerable destruction of user configuration. Widening `LOCAL_ONLY_KEYS` to
   cover it would mean redefining the fence's rationale to fit one new key.
3. **It is all-or-nothing.** `defaultDeckSettings()` replacement destroys every
   binding the user authored. A user whose only problem is one bad grid value
   loses everything.
4. **It is dominated by a cheaper mechanism.** §6.3's boot-time detector needs no
   server, no API, no identity, and no network — and it works when the server is
   down, which the reset flag by construction cannot.

### 0.2 Do NOT add a `muxplex config` (or any CLI) escape for deck settings

The deck's settings live in `localStorage` on the phone. `muxplex` runs on the
server. **There is no path from the CLI to that store.** `muxplex config reset`
resets `~/.config/muxplex/settings.json` and has precisely zero effect on
`deckSettings`. This is stated explicitly because "just add a CLI command" is the
obvious-looking answer that cannot work, and someone will propose it.

The CLI escape is the right answer for the other two surfaces — and it already
exists for both (§3). It is structurally unavailable for the one surface that
needs it.

### 0.3 Do NOT add a multi-finger tap, shake, or any other bare gesture

The backlog already reached this verdict and it is correct: a pointer-timer
gesture has no node in the accessibility tree, TalkBack cannot surface it, and a
gesture-only entry point is the precise cause of the 2026-07 "couldn't find it"
incident that produced the SETTINGS key in the first place (see `deck.js:1955`'s
own comment). Adding a second gesture would re-commit the original mistake in the
name of fixing it. Closed.

### 0.4 Do NOT raise the brightness floor above 10%

The floor exists and is 10% (`setBrightness`, `deck.js:2478`). Raising it removes
a capability someone legitimately wants (a propped phone at 3am) in order to fix a
problem that **non-persistence** fixes without removing anything — and
non-persistence is what the hardware sidecar already does, with a written
rationale describing this exact failure. See §5.1. Prevention here is the wrong
tool; the right one is to stop the bad state from surviving a restart.

---

## 1. The finding that shapes everything below

**Only one of the three surfaces can strand a user, and the reason is structural.**

| Surface | Where its settings live | Out-of-band editor | Can strand? |
|---|---|---|---|
| Hardware deck | `~/.config/muxplex-deck/config.json` | `muxplex-deck config` CLI, always available | **No** |
| Web UI | `~/.config/muxplex/settings.json` (server) | `muxplex config` CLI + browser address bar | **No** |
| **Soft deck** | `localStorage['muxplex-deck-settings']` on the phone | **none** | **Yes** |

The hardware deck and the web UI are safe for the same reason: their settings live
in a file that a second, unaffected tool can always reach. The soft deck's live in
a store with exactly one reader and one writer — the very page that the settings
can break.

That is the whole item. Every change specified below applies to
`muxplex/frontend/deck/` and nothing else.

---

## 2. Enumeration — every setting, from the code

### 2.1 Soft deck (`deck.js:824` `defaultDeckSettings`, validated in `mergeDeckSettings:847`)

| Key | Accepted range | Strands? | Mechanism |
|---|---|---|---|
| `version` | `1` | no | — |
| `sort` | `attention` \| `server` | no | ordering only |
| `pollIntervalMs` | 500–60000 | no | 60s stale is annoying, never unreachable |
| `gridOverride` | `{rows,cols}` each 1–12, product ≤ `N_MAX` (32) | **YES — two distinct ways** | §2.3 A and B |
| `dialCount` | 0–4 | contributing | reserves 100px (`DIAL_STRIP_H`), shrinking the content box toward `tooSmall` |
| `stripCount` | 0–4 | contributing | reserves 72px (`TOUCH_STRIP_H`), same |
| `brightness` | 10–100 | **YES** | §2.3 C |
| `bindings` | address → action | **YES (partial)** | §2.3 D |

### 2.2 The other two surfaces, for completeness

**Hardware deck** (`muxplex-deck/src/muxplex_deck/config.py:40` `DEFAULT_CONFIG`):
`server_url`, `key_file`, `ca_file`, `poll_interval`, `sort`, `focus_app`,
`controls`. None strands. Notably **brightness is not a config key at all** — it is
session-local and re-asserted to 100% on every bring-up
(`muxplex-deck/src/muxplex_deck/main.py:490-497`), with a comment that names this
exact hazard: *"writing a dimmed value to config.json would fight that deliberate
reset and could leave a deck that looks dead after a replug with the cause stored
invisibly in a file."* Binding every control to `none` is recoverable via
`muxplex-deck config reset controls`. A bad `server_url` yields an UNREACHABLE
screen and the same CLI fix.

**Web UI** (`muxplex/settings.py:34` `DEFAULT_SETTINGS`): the gear button is
present in both header variants (`frontend/index.html:50` and `:71`), so no
`viewMode`/`gridViewMode`/`sidebarOpen` value removes it. The root manifest is
`display: standalone`, which keeps browser-level navigation available in a way
`fullscreen` does not. `fontSize` has no server-side range validation, so a hostile
or buggy `PATCH` can set it absurdly — that degrades previews and the terminal, but
the settings dialog carries its own type scale and stays usable, and
`muxplex config set fontSize 14` fixes it regardless. **Nothing to build.**

### 2.3 The four stranding paths, with reproductions

All four were verified against the source, not inferred.

**A. `gridOverride` that produces a degenerate grid → no SETTINGS key AND no
long-press.** `reservedControlKeys(rows, cols)` (`deck.js:1091`) returns
`mode: 'degenerate'` when `rows*cols < 4` or when the three corner positions
collide — which is *every* `1×N` and *every* `N×1`. `computeKeyPlan`'s picker
branch (`deck.js:1493`) requires `hasControls && slots.length >= 2` to place the
SETTINGS key, and the long-press arms only when `key.dataset.role === 'view'`
(`deck.js:2003`), a role that does not exist on a degenerate grid. `2×2` is a
second case: it is `corners` mode, so long-press survives, but `slots.length === 1`
so the SETTINGS key is absent — leaving the accessibility-invisible path as the
only way in, which is the exact state the 2026-07 fix existed to eliminate.

**B. `gridOverride` too small for the viewport → a completely blank black screen.**
This is a live bug today, independent of this feature. `computeGridForShape`
(`deck.js:972`) returns `tooSmall: true` when `s < S_MIN` (72), with `rows`/`cols`
non-zero. `recomputeGrid` puts `.too-small` on `#deck-root` (`deck.js:2099`), and
`deck.css:72` (`#deck-root.too-small #deck-surface { display: none }`, specificity
0-2-1-0, beating `#deck-surface`'s 0-1-0-0) hides the surface. But `render()`'s
takeover guard (`deck.js:2241`) only fires on `grid.rows === 0 || grid.cols === 0`
— which `computeGridForShape` returns only when the *content box* is degenerate,
never for a too-small override. So `hideDisconnected()` runs, `renderKeys()` paints
into a `display: none` container, and the user sees **nothing at all**: no keys, no
message, no RETRY button. `deck.css:68`'s own comment claims "deck.js also switches
to the disconnected/message takeover in this case" — that code does not exist.

Verified by running the real exported functions:

```
$ cd muxplex/frontend/deck && node -e "…computeEffectiveGrid(844,390,{rows,cols})…"
1x1   tooSmall=false s=160 mode=degenerate  slots=1  settingsKey=false
1x4   tooSmall=false s=160 mode=degenerate  slots=4  settingsKey=false
4x1   tooSmall=false s=89  mode=degenerate  slots=4  settingsKey=false
2x2   tooSmall=false s=160 mode=corners     slots=1  settingsKey=false
2x3   tooSmall=false s=160 mode=bottom-row  slots=3  settingsKey=true
3x2   tooSmall=false s=123 mode=corners     slots=3  settingsKey=true
12x2  tooSmall=TRUE  s=22  mode=corners     slots=21 settingsKey=true   <- blank screen
2x12  tooSmall=TRUE  s=60  mode=corners     slots=21 settingsKey=true   <- blank screen
8x4   tooSmall=TRUE  s=39  mode=corners     slots=29 settingsKey=true   <- blank screen
4x8   tooSmall=false s=89  mode=corners     slots=29 settingsKey=true
```

`12×2` is 24 keys — inside `N_MAX` — and the settings form accepts it today
(`deck.js:3102` checks only `1..12` and `r*c <= N_MAX`). One `change` event on a
landscape phone produces a black screen with no way back except a URL the installed
PWA cannot type.

**C. `brightness` is self-sealing.** `applyBrightness()` (`deck.js:1905`) sets
`root.style.filter = 'brightness(0.1)'` on `#deck-root`. `#deck-settings` is a
**child** of `#deck-root` (`deck/index.html:78`, inside the `div#deck-root` that
closes at `:158`), and a CSS `filter` on an ancestor composites the entire subtree —
**a descendant cannot opt out.** So the Settings panel, the disconnected takeover,
and the SETTINGS key face are all dimmed by the value the user is trying to escape.
And the value persists: `saveDeckSettings` writes it, `mergeDeckSettings:877` reads
it back, so a reload does not clear it. With the default settings (no bindings, no
dials, no strip) the *only* control that raises brightness is inside that panel.

**D. `bindings` starve the picker's settings slot.** `sessionSlotIndices`
(`deck.js:1122`) excludes bound indices from the pool, and the picker takes the
SETTINGS key from `slots[0]` only when `slots.length >= 2`. Binding enough keys
drops it below 2 and the SETTINGS key vanishes. The reserved control keys
themselves are safe — `computeKeyPlan` paints bound faces first and then
`_setControlFace` overwrites `reserved.view/prev/next`, so VIEW cannot be bound
away and long-press survives. This vector therefore degrades to "long-press only,"
not to "no way in" — but see A: that is still the state the settings-discoverability
fix exists to prevent.

### 2.4 `?settings=1` — why it is not the mitigation it was signed off as

`checkURLEscapeHatch()` (`deck.js:3233`) is real and works from a browser tab.
`deck/manifest.json` is `"display": "fullscreen"` with `"start_url": "/deck/"`, so
an *installed* deck has no address bar and no way to reach a query parameter. The
backlog states this correctly. It stays exactly as it is — additive, free, and the
right answer for the tab case.

**One thing the builder must verify, because the answer changes how much this
matters (§8, S7):** on iOS, a Home Screen web app has historically had a WebKit
storage container separate from Safari's. If that is still true, then for an iOS
user the browser-tab `?reset=1` path was **never** able to clear the installed
deck's `localStorage`, and the fallback is weaker on iOS than on Android. Verify on
real hardware before relying on it in any user-facing text; if confirmed, correct
`BACKLOG.md` §4's characterization too.

---

## 3. What "unreachable" means, precisely enough to test

The Settings panel is opened by exactly three code paths. There are no others:

1. `onKeyTap` with `role === 'settings'` — the picker's SETTINGS key (`deck.js:2300`)
2. long-press on a key whose live `dataset.role === 'view'` (`deck.js:2003`)
3. `checkURLEscapeHatch()` at boot — **requires an address bar**

For an installed (fullscreen) deck, path 3 does not exist. So:

> **Definition.** For a given persisted `deckSettings` and a given viewport, the
> deck is **reachable** iff some sequence of taps on the rendered surface opens the
> Settings panel; it is **discoverable** iff at least one of those paths has a node
> in the accessibility tree.

Reduced to the code's own primitives, with `reserved = reservedControlKeys(rows, cols)`
and `slots = sessionSlotIndices(rows, cols, reserved, boundKeys)`:

| Condition | Level | Meaning |
|---|---|---|
| `tooSmall` | `none` | nothing renders at all |
| `reserved.mode === 'degenerate'` | `none` | no VIEW key ⇒ no long-press, and the picker gets no SETTINGS key |
| `slots.length < 2` | `longpress-only` | SETTINGS key absent; only the accessibility-invisible path remains |
| otherwise | `full` | both paths present |

**`full` is the only acceptable steady state.** `longpress-only` is treated as a
failure, not a degraded pass — that is the whole content of the 2026-07 incident.

This predicate is a pure function of four numbers and a dict. It is testable with
zero DOM, in the same style as every other function in `deck.js`'s pure-logic
section, over the exact fixture table printed in §2.3.

---

## 4. The design, in one paragraph

Stop `brightness` from persisting (matching the hardware, which already solved
this), move the dim filter off the recovery surfaces, refuse to *save* a grid shape
that has no settings entry, fix the blank-screen bug so the too-small takeover
actually appears and give it a SETTINGS button, and add a boot-time detector that —
when the persisted settings are unreachable — opens Settings, says why, and changes
nothing. Four changes, no new API, no new server settings key, no new gesture, and
no recovery UI that itself lives inside the grid.

---

## 5. Prevention versus recovery — the argument

The task is right that these are different amounts of work and not mutually
exclusive. They are also not interchangeable, and which one applies is decided per
vector by one question: **is the bad state decidable at write time?**

### 5.1 Brightness: neither prevention nor recovery — non-persistence

A floor (say, refuse anything below 35%) is the cheap prevention. Reject it: it
deletes the "phone left on a desk overnight" capability the feature was built for,
and it does not even fully work — at 35% in daylight the SETTINGS key is still hard
to hit, so you would be trading a real capability for a partial fix.

A recovery path is the expensive alternative, and every candidate has to be legible
at 10% to be used, which is circular.

The third option dominates both: **make the bad state not survive.** The hardware
sidecar already does exactly this, and its comment names this failure mode almost
word for word (`muxplex-deck/.../main.py:490-497`). The soft deck copied the
`brightness_up`/`brightness_down`/`brightness_cycle` actions from the hardware
catalog but *not* the safety property that makes them safe there. Restoring it:

- removes no capability — you can still dim to 10% for the evening
- costs one reload to undo, and a reload is something an installed PWA user can
  actually perform (unlike typing a URL)
- increases cross-surface parity rather than diverging further
- needs no new UI at all

The one honest cost: a permanently-propped phone re-brightens on every cold start.
That is visible, self-explaining, and self-correcting. Weighed against a state
whose only exit is a control you cannot see, it is not close.

This is a behavior change, so it is disclosed at the point of the control (§6.1) —
not silently, and not as a value that springs back after the fact.

### 5.2 `gridOverride` degenerate/2×2: prevention, because it is fully decidable

`reservedControlKeys(r, c).mode` and `sessionSlotIndices(...).length` are pure
functions of `(rows, cols)`. Whether a shape has a settings entry is knowable the
instant the user types it. Refusing at write time is strictly better than
recovering after: no bad state is ever created.

Is a capability lost? Effectively none. **A degenerate grid has no VIEW key at all,
which means no view switching and no paging — not just no Settings.** A `1×N` deck
can only ever show page 1 of view `all`. Refusing it removes a configuration that
was already non-functional for reasons that have nothing to do with this item. The
smallest shape that works is 6 keys with both dimensions ≥ 2 (`2×3` or `3×2`); the
error message says so.

### 5.3 `gridOverride` too small: recovery, because it is *not* fully decidable

`tooSmall` depends on the viewport, which changes with rotation and with the
device the same `localStorage` origin is used on. A write-time check would be
correct for the current viewport and wrong after a rotation. So this one needs a
runtime backstop — and the backstop is nearly free, because it is a bug fix the
codebase owes anyway (§2.3 B).

### 5.4 `bindings`: neither, plus the detector

Partially decidable (the bound count is known, the grid is not stable), and the
failure is `longpress-only` rather than `none`. Not worth a write-time refusal that
would have to be re-litigated on every resize. The boot detector covers it.

### 5.5 Why a detector is required regardless of how good the prevention is

**Prevention only helps future writes. Installs already carry bad values.** Any
deck that has already been set to `1×1`, `12×2`, or brightness 10 is stranded right
now, and a write-time guard shipped tomorrow does nothing for it. The boot detector
is not belt-and-braces; it is the migration path for state that already exists —
and it is the only mechanism in this document that helps a user who is stranded
today.

---

## 6. Implementation specification

All paths relative to `muxplex/`. No Python changes. No `/api/*` changes.

### 6.0 Constraints this design honors (verify each before merging)

- **`LOCAL_ONLY_KEYS` is untouched.** Nothing here writes server settings at all.
  The fence keeps its stated scope: keys naming a command or filesystem path the
  server executes or reads. §0.1 explains why the rejected direction would have
  forced that scope open.
- **`/api/*` gains nothing.** Additivity is satisfied vacuously.
- **`ACTION_CATALOG` is untouched.** It is a byte-for-byte mirror of
  `muxplex-deck`'s 19-action `ACTIONS` dict with a fixture tripwire in
  `test_deck.mjs`. This design deliberately does **not** add a `settings` action;
  doing so would either break that fixture or force a soft-deck-only action into a
  table whose entire purpose is cross-repo parity. (`STRIP_ACTION_CATALOG` is the
  established escape hatch if a future item genuinely needs one — this one does not.)
- **`muxplex-deck` is not modified.** Its behavior is the reference §6.1 converges on.
- **`checkURLEscapeHatch()` keeps working exactly as today.** Everything here is
  additive to it.
- **`mergeDeckSettings`'s recovery posture is preserved** — it stays a pure merge
  that drops bad *fields* rather than rejecting blobs. Policy lives in the form and
  the detector, not in the merge.

### 6.1 Change 1 — brightness stops persisting; the recovery surfaces leave the filtered subtree

**`deck.js`, pure-logic section:**

Add one exported function next to `exportSettingsJSON`:

```js
/**
 * The subset of deck settings that is written to storage / export.
 *
 * `brightness` is deliberately EXCLUDED -- it is session-local, exactly like
 * the hardware sidecar's (muxplex-deck/src/muxplex_deck/main.py:490-497,
 * "writing a dimmed value to config.json would ... leave a deck that looks
 * dead after a replug with the cause stored invisibly in a file"). A
 * persisted 10% is self-sealing on the soft deck for the same reason and
 * worse: the CSS filter that dims the surface also dims the Settings panel
 * that would undo it. See docs/plans/2026-08-06-settings-recovery-plan.md.
 */
function persistableDeckSettings(settings) { /* shallow copy minus brightness */ }
```

- `saveDeckSettings` (`:918`) serializes `persistableDeckSettings(settings)`.
- `exportSettingsJSON` (`:933`) serializes `persistableDeckSettings(settings)`.
- `mergeDeckSettings` (`:877-879`): **delete** the `incoming.brightness` branch.
  `out.brightness` stays at `defaultDeckSettings()`'s 100 always.
- `setBrightness` (`:2478`): keep the 10–100 clamp, **drop** the
  `saveDeckSettings(storage, deckSettings)` call, and comment why (nothing
  persistent changed).
- `defaultDeckSettings().brightness` stays `100`; its comment gains "session-local,
  never persisted."

**`deck.js`, DOM section — `applyBrightness()` (`:1905`):**

```js
root.style.setProperty('--deck-dim', String(deckSettings.brightness / 100));
// root.style.filter is no longer set here; deck.css decides WHICH elements dim.
```

Remove any residual inline `root.style.filter` assignment.

**`deck/deck.css`:**

```css
/* The dim applies to the deck's CONTENT, never to its recovery surfaces:
   #deck-settings and #deck-disconnected must stay legible at any brightness,
   because they are what a user at 10% is trying to reach. A CSS filter on an
   ancestor composites the whole subtree and a descendant cannot opt out --
   which is why this is applied per-element rather than on #deck-root. */
#deck-grid,
#deck-dial-strip,
#deck-touch-strip {
  filter: brightness(var(--deck-dim, 1));
}
```

**Target `#deck-grid`, not `#deck-surface`.** `#deck-surface` is
`display: contents` (`deck.css:78`) so it generates no box for a filter to apply
to. This is the single most likely way to implement Change 1 and have it silently
do nothing; smoke test S2 (§8) exists to catch it.

`#deck-root` keeps no filter, so `#deck-settings` and `#deck-disconnected` — both
its direct children — render unfiltered at every brightness.

**`deck/index.html`, brightness section (`:122-127`):** add one line of copy under
the slider: *"Session only — resets to 100% when the deck restarts, like the
hardware deck."* Disclosure at the point of the control, not a message after the
fact.

### 6.2 Change 2 — refuse an unreachable `gridOverride` at write time

**`deck.js`, pure-logic section, new exported function:**

```js
/**
 * Whether a grid shape leaves a way back into Settings.
 *
 * Deliberately passes an EMPTY boundIndices map: this asks about the SHAPE,
 * not about today's bindings. A shape that is only reachable because nothing
 * happens to be bound is a shape that strands the moment a binding is added.
 * @returns {{ok: boolean, reason: string}}   reason: '' | 'degenerate' | 'no-settings-slot'
 */
function gridOverrideReachability(rows, cols) {
  var reserved = reservedControlKeys(rows, cols);
  if (reserved.mode === 'degenerate') return { ok: false, reason: 'degenerate' };
  if (sessionSlotIndices(rows, cols, reserved, {}).length < 2) {
    return { ok: false, reason: 'no-settings-slot' };
  }
  return { ok: true, reason: '' };
}
```

Refuses `1×N`, `N×1`, and `2×2`. Accepts `2×3`, `3×2`, and everything larger.

**`deck.js`, `applyGridOverride` (`:3099`):** after the existing range checks, call
the predicate. On `ok: false`, write into a new `#settings-grid-error` element and
**return without saving**; on `ok: true`, clear the error and save as today.

Message text must name the constraint *and* the fix, e.g.:

> `3×1 leaves no room for the VIEW key, so the deck would have no way back into`
> `this panel — and no way to switch views or pages either. The smallest grid`
> `that works is 2×3.`

**`deck/index.html`:** add `<p id="settings-grid-error" class="settings-error"></p>`
inside the Grid layout `<section>`, matching the existing
`#settings-add-error` / `#settings-import-error` pattern.

**`deck.js`, import handler (`:3206`):** after a successful merge, if the result's
`gridOverride` fails the predicate, write a **warning** (not a refusal) into
`#settings-import-error` and still apply. Rationale: import is a restore path, and
refusing a whole blob over one field contradicts `mergeDeckSettings`'s documented
"recover as much as it safely can" posture. The panel stays open after import, so
the user is already standing in the place to fix it, and §6.3 catches it on the
next boot regardless.

`mergeDeckSettings` itself is **not** changed. Adding this policy there would alter
what `loadDeckSettings` returns for existing installs — silently repairing a value
the user chose, which the constraints forbid.

### 6.3 Change 3 — boot-time reachability detector: open Settings, explain, change nothing

**`deck.js`, pure-logic section, new exported function:**

```js
/**
 * Whether the persisted settings leave a usable way into the Settings panel,
 * for the CURRENT grid. See docs/plans/2026-08-06-settings-recovery-plan.md §3
 * for the definition and the three (and only three) code paths that open the
 * panel.
 *
 * `level`:
 *   'full'           -- SETTINGS key present on the picker AND long-press armed
 *   'longpress-only' -- only the accessibility-invisible path remains
 *   'none'           -- no path at all
 * Anything other than 'full' is a failure: 'longpress-only' is the exact state
 * the 2026-07 settings-discoverability fix exists to eliminate.
 *
 * @param {{rows:number, cols:number, tooSmall:boolean,
 *          boundKeys:Object<number,string>, gridOverride:?{rows:number,cols:number}}} p
 * @returns {{level:string, reasons:string[]}}
 */
function settingsReachability(p) { /* ... */ }
```

Reason codes (stable strings — the DOM layer maps them to copy):

| Code | Emitted when |
|---|---|
| `grid-too-small` | `p.tooSmall` |
| `grid-degenerate` | `reservedControlKeys(...).mode === 'degenerate'` |
| `grid-too-few-keys` | `slots.length < 2` **and** the same shape with no bindings would also be `< 2` |
| `bindings-consumed-slots` | `slots.length < 2` **but** the unbound shape would have been `≥ 2` |

The last two are distinguished by evaluating `sessionSlotIndices` twice (once with
`p.boundKeys`, once with `{}`) so the banner can name the actual culprit instead of
blaming the grid for a binding problem.

**`deck.js`, `boot()` (`:3250`):** after `recomputeGrid()` / `render()`, before
`poll()`:

```js
var reach = settingsReachability({
  rows: grid.rows, cols: grid.cols, tooSmall: !!grid.tooSmall,
  boundKeys: boundKeys, gridOverride: deckSettings.gridOverride,
});
if (wantsSettings) openSettings(null);
else if (reach.level !== 'full') openSettings(reach.reasons);
```

**`openSettings(reasons)` (`:2988`)** gains an optional argument. When `reasons` is
a non-empty array it populates and unhides `#settings-recovery-banner`; otherwise
it hides it.

**No repair, ever.** The banner explains; `deckSettings` is not modified. Its copy
names the offending value and the two ways out — fix the field, or "Reset to
defaults" (the existing `#settings-reset` button). Example for `grid-too-small`:

> `Your saved 12×2 grid is too small to draw on this screen, so the deck could`
> `not render. Settings has been opened because nothing else would have been`
> `visible. Nothing has been changed — pick a larger cell count or tap Auto.`

**`closeSettings()` is not changed.** If the user closes with a still-unreachable
config, the close is allowed and the detector fires again on the next cold start.
Deliberately no "you're still stranded" warning on close: `DESIGN_SOFTDECK.md` §7
establishes that this surface has no toast/banner vocabulary outside the two
whole-surface takeovers, and inventing one for an edge case the user just chose is
worse than a bounded, self-explaining loop.

**`deck/index.html`:** add, as the first child of `#deck-settings`, before the
BACK button:

```html
<div id="settings-recovery-banner" class="hidden" role="alert"></div>
```

`role="alert"` gives it an accessibility-tree presence — the property whose absence
is the through-line of this whole area of work.

### 6.4 Change 4 — fix the blank-screen bug; give the takeover a SETTINGS button

**`deck.js`, `render()` (`:2241`):**

```js
if (!grid || grid.rows === 0 || grid.cols === 0 || grid.tooSmall) {
  showDisconnected(takeoverMessage(grid, deckSettings.gridOverride));
  return;
}
```

`takeoverMessage` names the cause when an override is responsible (*"This screen is
too small for a 12×2 grid."*) and falls back to today's *"Screen too small for the
deck."* otherwise. Without this, `.too-small` hides the surface while
`#deck-disconnected` stays hidden and the user gets a black rectangle (§2.3 B).

**`deck/index.html`, inside `#deck-disconnected`:** wrap the existing RETRY button
and a new one:

```html
<div class="deck-takeover-actions">
  <button type="button" id="deck-retry" class="deck-takeover-key">RETRY</button>
  <button type="button" id="deck-settings-open" class="deck-takeover-key">SETTINGS</button>
</div>
```

**`deck/deck.css`:** `.deck-takeover-actions { display: flex; gap: 24px; }` and move
`#deck-retry`'s existing face styling onto `.deck-takeover-key` so both buttons share
it. Two 160×160 faces must sit side by side, not stacked — a landscape phone is
~390px tall and the column layout would overflow.

**`deck.js`:** wire `#deck-settings-open` to `openSettings(null)` beside the existing
`retryButton` listener (`:2270`).

Shown unconditionally, including for ordinary connection failures. That is a
deliberate simplification over a conditional: the takeover screen is by definition
non-functional, it spends no key slot and breaks no illusion because it replaces the
grid entirely, and one button with no branch is easier to keep correct than two
states. It is also the one affordance in this design that no grid shape, no binding,
and (after §6.1) no brightness value can remove.

### 6.5 Exports and cross-file guards

Add to `module.exports` (`:3272`): `persistableDeckSettings`,
`gridOverrideReachability`, `settingsReachability`. `test_deck.mjs`'s
"deck.js exports all pure functions" test enumerates this list and must be extended
in the same commit.

New top-level bindings must not collide with `app.js` / `terminal.js` globals — see
`AGENTS.md`, "Frontend classic scripts share one global scope." `test_shared_scope.mjs`
covers this automatically; the three names above are already unique.

Any class name `deck.js` *applies* via `classList` must exist in `deck.css`, or
`test_css_class_definitions.mjs` fails. The new classes here
(`.deck-takeover-actions`, `.deck-takeover-key`) are applied in HTML rather than JS,
so they fall outside that test's scope — define them anyway and note it.

---

## 7. Files touched

| File | Change |
|---|---|
| `frontend/deck/deck.js` | `persistableDeckSettings`, `gridOverrideReachability`, `settingsReachability` (new, pure, exported); `saveDeckSettings`/`exportSettingsJSON`/`mergeDeckSettings`/`setBrightness`/`applyBrightness`/`applyGridOverride`/import handler/`openSettings`/`render`/`boot` (modified); one new listener |
| `frontend/deck/index.html` | `#settings-recovery-banner`, `#settings-grid-error`, `#deck-settings-open`, `.deck-takeover-actions` wrapper, brightness disclosure copy |
| `frontend/deck/deck.css` | per-element `--deck-dim` filter; `.deck-takeover-actions` / `.deck-takeover-key` |
| `frontend/tests/test_deck.mjs` | new table-driven tests (§8) + exports list |
| `docs/BACKLOG.md` | delete item 4 (it graduates to this file) |

Not touched: any Python file, any `/api/*` route, `settings.py`, `docs/API_SEMANTICS.md`,
`muxplex-deck/**`, `CHANGELOG.md` and version numbers (release-owner territory per
`AGENTS.md`).

---

## 8. Evidence requirements

A recovery path that cannot be tested is a claim, not a feature. Each item below is
pass/fail with a named artifact.

### Unit — `node --test frontend/tests/*.mjs` (use the glob; see `AGENTS.md`)

| # | Assertion |
|---|---|
| U1 | `settingsReachability` returns the §3 level for every row of §2.3's fixture table (`1×1`, `1×4`, `4×1`, `2×2`, `2×3`, `3×2`, `12×2`, `2×12`, `8×4`, `4×8`) — the table is the test's data, verbatim |
| U2 | `settingsReachability` distinguishes `grid-too-few-keys` from `bindings-consumed-slots`: same `3×2` grid, once bare (`full`) and once with `key.1`/`key.2` bound (`longpress-only`, reason `bindings-consumed-slots`) |
| U3 | `gridOverrideReachability` is `false` for every degenerate shape and for `2×2`; `true` for `2×3` and `3×2` |
| U4 | `persistableDeckSettings` output has no `brightness` key; every other key round-trips unchanged |
| U5 | `mergeDeckSettings` ignores an incoming `brightness: 10` and yields `100` |
| U6 | `JSON.parse(exportSettingsJSON(s))` has no `brightness` key |
| U7 | exports list in "deck.js exports all pure functions" includes the three new names |
| U8 | `test_shared_scope.mjs` and `test_css_class_definitions.mjs` still pass unmodified |

### Smoke — real installed PWA on a real phone, one landscape device minimum

These are the parts no unit test in this repo can reach (`test_deck.mjs` is
deliberately DOM-free). Run against a real muxplex, with the deck **installed to the
home screen**, not in a tab — the tab case is the one that already worked.

| # | Scenario | Pass condition |
|---|---|---|
| S1 | Set `12×2` via Settings → close | Takeover message naming the 12×2 grid appears. **Not a black screen.** SETTINGS button opens the panel. |
| S2 | Set brightness to 10 → open Settings | Panel is at full brightness and legible; the grid behind it is dim. (Catches the `display: contents` trap in §6.1.) |
| S3 | Set brightness to 10 → force-quit the PWA → reopen | Deck is at 100%. `localStorage` blob contains no `brightness` key. |
| S4 | Hand-write `{"gridOverride":{"rows":1,"cols":1}}` into `localStorage` via devtools → cold start | Settings opens automatically with the banner naming `gridOverride`; **`localStorage` is byte-identical afterward** (nothing repaired). |
| S5 | Type `2` / `2` into the grid fields | Inline refusal naming 2×3 as the minimum; `localStorage` unchanged. |
| S6 | Open `/deck/?settings=1` and `/deck/?reset=1` in a browser tab | Behaves exactly as before this change. |
| S7 | **iOS only, information-gathering:** set a bad value in the installed deck, then open `/deck/?reset=1` in Safari, then reopen the installed deck | Record whether the reset took. Determines whether the browser-tab fallback exists on iOS at all (§2.4). Not a gate on merge — a gate on any user-facing text that claims it does. |

### Regression

`make test` (Python suite, in a DTU — never on a host running a live muxplex) must
stay green. It exercises no line of this change; run it to prove that, not to prove
the change works.

---

## 9. Answers to the backlog's open questions

**"Does a brightness floor apply narrowly (Settings panel only) or broadly (the
SETTINGS key face)?"** — Neither. The framing assumes brightness must persist. It
should not (§5.1). Within a session the dim is scoped to the grid and the strips, so
the panel and the takeover stay legible; across sessions the value is simply gone.
The key-face half of the question dissolves: a fresh load is always at 100%.

**"Is a `gridOverride` that produces a degenerate grid something Settings should
refuse to save?"** — Yes, for degenerate and `2×2`, because those are decidable from
`(rows, cols)` alone (§5.2). No for too-small, because that depends on the viewport
and must be caught at runtime instead (§5.3). And a detector is required on top of
both, because prevention does nothing for the installs that are already broken (§5.5).

**"Does the dashboard-reset direction need new API surface, or can it be expressed
as the phone re-reading `defaultDeckSettings()` next time it loads?"** — It needs new
API surface, and it should not be built either way (§0.1). The second half of the
question is the good idea hiding inside it: *the phone re-reading its own settings at
load is the recovery moment.* §6.3 uses exactly that moment, driven entirely by
local state, with no server, no identity, and no destruction of the user's bindings.

---

## 10. Ordering, and why this precedes backlog item 2

Item 2 (a settings menu inside the soft deck) ships more ways to reach a bad state.
Every change here is a property of the *escape*, not of any particular setting, so
it holds for settings that do not exist yet:

- the takeover's SETTINGS button is outside the grid
- the detector is a function of the rendered geometry, not of a key list
- the brightness rule is about persistence, not about a slider

The one obligation item 2 inherits: **any new soft-deck setting must be added to
§2.1's table with an explicit strands/does-not-strand verdict, and if it can strand,
to `settingsReachability`'s reason codes.** That is the standing rule this document
leaves behind.

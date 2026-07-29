# Soft Deck — Responsive & Touch Strategy

**Status:** DESIGN ONLY. No implementation code in this document.
**Scope:** Size classes, touch-target math, screen-wake strategy, standalone/manifest
requirements, orientation, tap feedback. Owns *how things adapt*; does not own page
structure, component internals, or the design-token palette.
**Companion:** `muxplex-deck/docs/SOFT_DECK_DESIGN.md` (architecture decision —
same-origin `/deck/` route, installed to home screen, not native).
**Ground truth as read:** `muxplex/frontend/{index.html,manifest.json,style.css}`
(2,552-line stylesheet, 9 existing media queries, `:root` palette),
MDN Screen Wake Lock API, MDN `orientation` manifest member, WebKit bug 255363,
W3C Screen Wake Lock spec, W3C Screen Orientation spec.

---

## 0. The premise this document reasons from

The device is **peripheral hardware, not a destination**. It is propped up, screen
on, for hours. The user is looking at a laptop; the deck is in peripheral vision.
They glance, reach over, and tap.

Three consequences that drive everything below:

1. **The eye arrives before the hand.** Legibility at 300–450 mm in *peripheral*
   vision is the constraint, not legibility at reading distance. This inflates the
   minimum useful tile size well past the touch-target floor.
2. **The hand arrives unaimed.** The user is not looking at the finger. Fitts's-law
   reasoning about acquisition time is the wrong frame; *hit-rate under divided
   attention* is the right one, and it does not asymptote at 48 px — it keeps
   improving with target size long after the accessibility floor is satisfied.
3. **Nothing here is a document.** No reading flow, no text selection, no deep
   scroll, no forms. Almost every default the mobile web platform gives us is
   tuned for a document and is wrong here. Most of the work below is *subtraction*.

---

## 1. Size classes

### 1.1 The call: column count is computed, not classed

The instinct is to write breakpoints. Don't. The only question width answers is
**"how many legible tiles fit?"** — and CSS grid answers that intrinsically with a
single declaration and zero media queries:

```
grid-template-columns: repeat(auto-fill, minmax(var(--tile-min), 1fr));
--tile-min: 136px;   gap: 8px;   container padding: 8px (see §4 for insets)
```

Column count `n` is then `floor((W − 8) / 144)` for viewport width `W`.
Crossovers land at:

| Columns | Viewport width ≥ |
|---|---|
| 2 | 296 px |
| 3 | 440 px |
| 4 | 584 px |
| 5 | 728 px |
| 6 | 872 px |
| 7 | 1016 px |

Resulting tile widths across the entire supported range:

| Viewport width | Columns | Tile width |
|---|---|---|
| 320 | 2 | 148 px |
| 390 | 2 | 183 px |
| 430 | 2 | 203 px |
| 440 | 3 | 136 px |
| 768 | 5 | 144 px |
| 844 | 5 | 159 px |
| 932 | 6 | 146 px |
| 1024 | 7 | 137 px |
| 1366 | 9 | 143 px |

**Tile width never leaves the 136–203 px band.** That is a 1.5× range across a 4.3×
range of viewport widths. This is the whole point: the intrinsic rule *normalizes*
tile geometry, so nothing downstream of it needs to be responsive.

Two things fall out of that, and they are the reason this approach is worth the
paragraph of arithmetic:

- **No fluid typography.** Over a 1.5× width band, `clamp()` and container queries
  have nothing left to do. Fixed pixel type sizes are correct here — and simpler.
- **Every phone in portrait is 2 columns.** 320 through 430 px all land on 2. The
  grid does not reflow as you move between phones, which preserves the spatial
  constancy that `SOFT_DECK_DESIGN.md` §4.3 identifies as the thing that makes a
  deck a deck.

**Why 136 px specifically.** It is the *legibility* floor, not the touch floor. At
136 px with 8 px padding, 120 px of content holds ~16 characters of 14 px system-ui
before ellipsis. Session names in play (`amplifier-main` = 14, `muxplex-deck` = 12,
`deckwork` = 8) fit. This is the soft deck's analogue of the physical deck's
`MAX_SESSION_LABEL_CHARS`: **~16 characters, versus ~7 on hardware.** Raise
`--tile-min` to hold 2 columns further up the range; lower it at your own risk.

### 1.2 The call: exactly two classes, keyed on **height**

Width is handled. What width *cannot* express is whether the tile has room for a
preview band. That is a height question, and it needs exactly one breakpoint.

| Class | Query | Tile anatomy | Rationale |
|---|---|---|---|
| **Regular** | *(default)* | `aspect-ratio: 4/3`, three zones: NAME / PREVIEW / STATE | Enough vertical room that the preview is genuine content, not texture |
| **Short** | `@media (max-height: 520px)` | Fixed `height: 72px`, two zones: NAME / STATE, preview suppressed | Vertical room is the scarce resource; maximize tile *count* instead |

**Which real configurations hit Short:** phones in landscape (390–430 px tall), and
nothing else. Tablets in landscape are 744–834 px tall and stay Regular. That is the
correct partition, and it arrives without naming a single device.

This is also the direct answer to "a phone in landscape and a small tablet in
portrait may be the same class" — by *width* they are (844 and 768 both give 5
columns, 159 px and 144 px tiles). By *height* they are not, and height is the axis
that changes what a tile can contain. **Width and height are answering different
questions and should not share a breakpoint system.**

Visible-tile counts, worked:

| Configuration | Class | Grid | Tiles fully visible |
|---|---|---|---|
| 320 × 568 | Regular | 2 × 111 px tall | ~8 |
| 390 × 844 | Regular | 2 × 137 px tall | ~9 |
| 844 × 390 | **Short** | 5 × 72 px tall | ~20 |
| 768 × 1024 | Regular | 5 × 108 px tall | ~40 |
| 1024 × 768 | Regular | 7 × 103 px tall | ~42 |

Against the stated 5–30 session range: portrait phone scrolls, everything else does
not. Acceptable, and Short mode makes landscape-on-a-phone the *best* glanceable
configuration — which is exactly the posture a propped device is in.

### 1.3 What this replaces

The existing PWA has four width breakpoints for its grid (`style.css:359`, `:365`,
`:375`, `:390`) and collapses to a single-column flex list below 599 px. **Do not
port that.** It is correct for a terminal-preview app where each tile is a document
you might read; it is wrong for a control surface where each tile is a button. The
deck's grid is one `auto-fill` declaration and one height query. Five media queries
become one.

---

## 2. Touch targets

### 2.1 The floor, cited

| Source | Requirement | Notes |
|---|---|---|
| Apple *Human Interface Guidelines* | **44 × 44 pt** minimum | pt ≈ CSS px on iOS |
| Material Design (Android) accessibility | **48 × 48 dp** minimum | dp ≈ CSS px |
| WCAG 2.2 SC 2.5.8 *Target Size (Minimum)*, **AA** | 24 × 24 CSS px | Floor, with exceptions |
| WCAG 2.1 SC 2.5.5 *Target Size (Enhanced)*, **AAA** | 44 × 44 CSS px | |

**Adopted floor: 48 × 48 CSS px.** It satisfies all four simultaneously, and it is
the larger of the two platform guidelines, so a single number covers both targets.

### 2.2 What that implies at the smallest viewport

The floor is not the design target. At the smallest supported viewport (320 px
wide), the grid produces:

| Element | Size | vs 48 px floor |
|---|---|---|
| Session tile, Regular class | 148 × 111 px | 3.1× / 2.3× linear, **7.1× area** |
| Session tile, smallest possible (Regular, 3-col crossover at W=440) | 136 × 102 px | 2.8× / 2.1×, **6.0× area** |
| Session tile, Short class | 136–203 × 72 px | 2.8× / 1.5×, **4.3× area** |
| Header controls (view button, toggle-last chip, wake indicator) | 48 × 48 px | 1.0× — at floor |
| Inter-tile gap | 8 px | see below |

**The tile is 4–7× the accessibility floor by area, deliberately.** Justification is
§0's premise 2: the hand arrives unaimed, and each tap is consequential. Sizing to
the floor would be sizing to the wrong requirement — the floor exists so a person
*aiming* at a control can hit it, not so a person *not looking* can.

**Header height is 56 px, not the PWA's 44 px** (`--header-height` at `style.css`).
A 44 px bar cannot contain a 48 px target. The deck deviates; state it in the
stylesheet so nobody "fixes" it back.

**On the 8 px gap.** WCAG 2.5.8 permits sub-minimum targets when spacing
compensates; we are nowhere near needing that exception. 8 px is chosen for
*visual* separation — enough that two tiles never read as one control at a glance —
and the mis-tap protection comes from target size, not from the gutter. Widening
the gutter would cost columns for no measured benefit.

### 2.3 Mis-tap policy

Taps are consequential; the honest question is what happens when one is wrong.

**Rejected:** a confirmation step. It converts the product's entire value
proposition (one tap) into two taps for a 100 %-cost-on-every-interaction defence
against an occasional error. **Rejected:** long-press-to-activate. Same objection,
plus it defeats glance-and-tap.

**Adopted: make every tap on this surface cheap to reverse, and let the undo be an
existing feature.** The `toggle_last` chip in the header (`SOFT_DECK_DESIGN.md`
§4.4) *is* the undo — a mis-tap is corrected by one tap on a control that already
had to exist. Nothing new is built.

This yields a hard constraint that belongs here and not in a later document:

> **No irreversible action may be reachable from a single tap on the deck
> surface.** Create, delete and clear-bell (`SOFT_DECK_DESIGN.md` §12/OQ6) are
> either absent, or behind an explicitly non-tap gesture. The one-tap grid is
> safe *only because* every one-tap outcome is one tap from being undone.

---

## 3. Screen-wake strategy

### 3.1 The call: always-on, with a truthful indicator that doubles as the toggle

Not a settings screen. Not a hidden preference. **One element in the header** that
reports actual wake state and, when tapped, toggles it:

| State | Reads | Meaning |
|---|---|---|
| Held | `awake` | Sentinel is live and unreleased |
| Refused | `asleep — tap to retry` | Request rejected; reason logged, not surfaced |
| Off by user | `asleep` | Explicitly toggled off |
| Unsupported | *(element hidden)* | `navigator.wakeLock` absent |

Two reasons this is one element rather than two:

- MDN's own guidance on this API: *"It's a good idea to show some feedback on the
  interface to show if wake lock is active and a way for the user to disable it if
  they wish."* Status **and** control are both called for.
- This project has been bitten five times by *stale state reported as current*
  (`muxplex-deck` v0.9.5 regression net). A wake lock that has silently died while
  the UI implies it is held is precisely that failure. The indicator must read the
  sentinel, never a variable set at request time.

Battery is the reason the toggle exists at all. An unplugged phone holding its
screen on for hours is a real cost, and the user is the only one who knows whether
it is plugged in.

### 3.2 Lifecycle — the parts that are not optional

Per MDN and the W3C spec:

- **Only visible, fully-active documents may hold a lock.** A request from a hidden
  document rejects with `NotAllowedError`.
- **The lock is auto-released when the document becomes hidden, and is never
  auto-reacquired.** The spec is explicit: *"make sure to re-acquire screen wake
  lock if necessary when document becomes active (listen for `visibilitychange`)."*
- The UA may refuse or revoke for **power-saving mode or low battery**. Requests
  must be wrapped and the rejection surfaced, not swallowed.
- `WakeLockSentinel` is single-use — after release, request a new one.

Required sequence:

1. On load, if visible → request. Store the sentinel.
2. Subscribe to `sentinel.onrelease` → update the indicator immediately. The system
   can release without a `visibilitychange`.
3. On `visibilitychange` → **hidden:** drop the sentinel reference and stop polling
   (`SOFT_DECK_DESIGN.md` §7.1). **Visible:** re-request *and* fire an immediate
   `/api/view` fetch before resuming the 2 s poll.
4. Every request in `try`/`catch`; on rejection set `asleep — tap to retry`.

**Step 3's immediate fetch is not a nicety.** Without it, a deck returning to the
foreground shows up to 2 s of stale tiles with no indication they are stale — the
same failure class as the wake indicator lying. Re-foregrounding must re-sync
before it re-renders.

### 3.3 The iOS problem — this one will bite

Two findings, both verified against primary sources, both specific to our exact
deployment (installed to home screen):

1. **WebKit bug 255363** (filed 2023-04-12): *"In iOS Wake Lock does not work after
   'visibilitychange' event to re-acquire wake lock if user goes to home screen. If
   apps are changed without going to home screen, then wake lock is re-acquired or
   stays active."* The re-acquire path in §3.2 step 3 — the load-bearing one — was
   the broken one.
2. The same bug reproduced specifically as **browser-mode works, installed-PWA-mode
   does not** (NoSleep.js #156, iOS 16.4). Per Progressier's capability tracking,
   **Apple fixed this in iOS 18.4.**

Consequences, stated plainly:

- **The soft deck has an effective floor of iOS 18.4 for its single most important
  behaviour.** Below that, on an installed PWA, the screen will sleep after the
  first home-screen round-trip and will not recover without a manual toggle.
- **This is the one place where the "iOS is free" argument in
  `SOFT_DECK_DESIGN.md` §7 has a real asterisk.** It does not change the
  recommendation — the fix shipped, and it shipped upstream rather than requiring
  us to build anything — but "verify the iOS version before concluding the deck is
  broken" belongs in the runbook.
- **Degradation, if the device is below 18.4:** the indicator reads
  `asleep — tap to retry` and tapping re-arms it. Ugly, honest, one tap. Do not
  add a `NoSleep.js`-style looping-silent-video workaround. It is a hack against a
  bug Apple has fixed, it burns battery and decode hardware continuously, and it
  would be dead code the moment the device updates.

Android Chrome has no equivalent defect; the §3.2 sequence works as specified.

### 3.4 The other power lever, which is free

The palette is already near-black (`--bg: #0D1117`). On an OLED phone — which every
plausible propped device is — that is a material and continuous power saving versus
a light UI, for zero design cost. It is worth naming so nobody later proposes a
light theme for this surface without pricing it.

**Rejected: auto-dimming the UI after idle.** The deck's only job is to be
glanceable. A surface that degrades its own legibility when un-touched has
optimised against its purpose. OS brightness is the user's control and it works.

---

## 4. Standalone mode, safe areas, and the manifest

### 4.1 Viewport meta

```
width=device-width, initial-scale=1, viewport-fit=cover
```

`viewport-fit=cover` is **mandatory** — without it every `env(safe-area-inset-*)`
resolves to `0px` and the notch/home-indicator handling below silently does nothing.
The existing `index.html` omits it (it has one `env()` use at `style.css:2507`,
which is therefore inert on iOS).

**Deliberate deviation from the existing app: no `user-scalable=no`, no
`maximum-scale=1`.** The PWA sets both (`index.html:5`). Three reasons not to:

1. **iOS Safari has ignored `user-scalable=no` since iOS 10.** Keeping it produces
   *divergent* behaviour between the two target platforms for zero benefit — which
   is the specific thing this document exists to eliminate.
2. It is a WCAG 1.4.4 (Resize Text) failure.
3. It does not solve the actual problem. The real hazard on a propped device is
   accidental **double-tap zoom** and the 300 ms tap delay that comes with it —
   both of which `touch-action: manipulation` on tiles kills cleanly and without
   disabling deliberate pinch. Accidental pinch needs two fingers and is rare;
   accidental double-tap is one finger and is not.

### 4.2 Safe-area insets

| Edge | Treatment | Why |
|---|---|---|
| Top | Header `padding-top: env(safe-area-inset-top, 0px)` | Status bar overlay (see §4.3) |
| Left / Right | Grid container `padding-inline: max(8px, env(safe-area-inset-left/right, 0px))` | **The forgotten one.** A notched phone in landscape — the propped posture — puts a ~44 px inset on one side |
| Bottom | Grid container `padding-bottom: max(8px, env(safe-area-inset-bottom, 0px))` | Home indicator; already the convention at `style.css:2507` |

The landscape side-inset is where naive implementations break, and it is where the
intrinsic grid pays for itself again: losing 44 px of width simply drops a column.
No breakpoint fires, no rule is needed, the layout is correct by construction.

Use `100dvh` with a `100vh` fallback, matching the existing convention
(`style.css:123–124`). Rotation on iOS resizes the viewport in stages; `dvh` handles
it, `vh` does not.

### 4.3 Status bar

**Call: `apple-mobile-web-app-status-bar-style: black-translucent`,** matching the
existing app, with explicit top-inset padding.

The alternative (`default`) lets iOS reserve the strip itself and saves us the
padding — but it renders an opaque light bar above a `#0D1117` page, which on a
propped deck reads as a rendering bug. `black-translucent` also keeps the **clock
and battery indicator visible over our background**, which on a device whose entire
job is to be glanced at is genuinely useful, not merely tolerable.

### 4.4 Manifest — `/deck/manifest.json`

Required members, with the non-obvious ones justified:

| Member | Value | Why |
|---|---|---|
| `id` | `"/deck/"` | Explicit, stable app identity. Without it Chrome derives id from `start_url`, so a later `start_url` change would orphan the installed app |
| `start_url` | `"/deck/"` | |
| `scope` | `"/deck/"` | See warning below |
| `name` / `short_name` | `"muxplex deck"` / `"deck"` | Must differ from the root app or the home screen shows two identically-labelled icons |
| `display` | `"standalone"` | |
| `orientation` | `"any"` | See §5 — an explicit decision, not a default |
| `background_color`, `theme_color` | `#0D1117` | Matches root app |
| `icons` | 192 `any`, 512 `maskable`, 512 `any` | Chromium installability criteria |

**`scope: "/deck/"` has a consequence worth flagging.** Any navigation to `/`
leaves scope and opens in a browser tab rather than in the installed app. That is
*desirable* — the deck stays a deck — but it collides head-on with the known login
redirect in `SOFT_DECK_DESIGN.md` §8.4: `POST /login` unconditionally 303s to `/`.
On an installed deck, a cold unauthenticated launch will therefore **eject the user
out of the standalone app into a browser tab showing the terminal app.** The
`?next=` server fix (§8.4 option 3) is not merely a papercut fix; in standalone
mode it is the difference between "log in, tap the icon again" and "log in, get
thrown into a different app in a different window." Recommend taking it.

**Required distinct icon art.** Same palette, different glyph (a grid, not the
wordmark). Two identical icons on a home screen is a usability failure, not a
cosmetic one — and it is the strongest practical argument for the "one icon +
manifest `shortcuts`" alternative in `SOFT_DECK_DESIGN.md` OQ4. If distinct art is
not going to be produced, take the single-icon route instead.

### 4.5 iOS ignores most of the manifest — the concrete divergence list

This is the section that will save debugging time.

| Concern | Android / Chrome | iOS / Safari | What to ship |
|---|---|---|---|
| Home-screen icon | `manifest.icons` | **Manifest icons ignored** — uses `<link rel="apple-touch-icon">` | Both. A 180×180 `apple-touch-icon` **in `/deck/`**, or iOS reuses the root app's icon |
| Home-screen label | `manifest.short_name` | **Ignored** — uses `<meta name="apple-mobile-web-app-title">` | Both |
| Theme / background colour | `theme_color`, `background_color` | **Ignored** — uses status-bar style + page background | Both; page background must be set in CSS regardless |
| Orientation | `manifest.orientation` honoured | **Not supported** (MDN: "Limited availability… not Baseline") | §5 — don't rely on it |
| Install prompt | Automatic; `beforeinstallprompt` lets the page trigger it | **No `beforeinstallprompt`** — manual Share → Add to Home Screen | Feature-detect; no in-page install button on iOS |
| Haptics | `navigator.vibrate()` | **Not implemented** | Feature-detect; visual feedback carries it (§6) |
| Long-press callout | `user-select: none` | Also needs `-webkit-touch-callout: none` | Both properties |
| Wake lock in installed PWA | Works | **Broken below iOS 18.4** (§3.3) | Indicator + retry |
| `user-scalable=no` | Honoured | **Ignored since iOS 10** | Don't ship it (§4.1) |

---

## 5. Orientation

### 5.1 The call: do not lock. Reflow.

Not because reflowing is better in principle — on a propped device, accidental
rotation genuinely is worse than a fixed layout — but because **a reliable
cross-platform lock does not exist**, and a half-working one is worse than none.

- `manifest.orientation` — MDN classifies it **"Limited availability… not Baseline
  because it does not work in some of the most widely-used browsers."** Chrome
  honours it for installed apps; iOS Safari does not support it.
- `screen.orientation.lock()` — not implemented in Safari on any platform. On
  Chrome Android the W3C spec makes fullscreen a pre-lock condition, so using it
  would mean putting the deck in fullscreen display mode purely to win a lock.

Shipping `"orientation": "portrait"` therefore buys a lock on Android and nothing
on iOS: **two devices behaving differently, for a feature that only helps if it is
universal.** Set `"orientation": "any"` explicitly so the next reader knows this was
decided, not defaulted.

### 5.2 What we do instead

**Point at the OS rotation lock.** It is one tap in Control Centre / Quick
Settings, it works on both platforms, and it cannot be beaten by a web page. This
belongs in the setup runbook alongside "Add to Home Screen" — it is a
one-time-forever step, not an ongoing chore.

### 5.3 Making the reflow cheap enough not to matter

Because the lock is unavailable, the reflow has to be good:

- **Column count changes; tile identity does not.** `auto-fill` recomputes columns;
  sort order is `server` (stable, per `SOFT_DECK_DESIGN.md` §4.3) so no tile moves
  relative to another. Rotation re-flows the same sequence into a different number
  of columns.
- **Portrait ↔ landscape on a phone crosses the Short/Regular boundary** (§1.2), so
  the preview band appears/disappears. This is the largest visual change rotation
  causes, and it is the correct one — landscape has less height and more tiles.
- **Do not animate the reflow.** Grid re-layout on rotation should be instant.
  Animating it makes an already-disorienting event longer.
- **Do not attempt to preserve scroll offset across rotation.** With ≤30 sessions
  and 8–42 tiles visible, scroll offset is usually 0. Preserving it is
  proportionally more code than it is worth, and restoring a stale offset into a
  differently-shaped grid is its own bug class.

---

## 6. Tap feedback

### 6.1 The requirement

No hover exists. The user needs confirmation the tap registered **before** the
`POST /connect` round-trip resolves — the platform's own touch-delay heuristics
alone will not deliver that, and a 2 s poll certainly will not.

### 6.2 Three layers

**Layer 1 — Press (0 ms). Pure presentational.**
`pointerdown` adds `.is-pressed`; `pointerup` / `pointercancel` removes it. Renders
as a background lift plus ~2 % scale-down.

Why `pointerdown` and not CSS `:active`: on iOS Safari `:active` does not apply on
touch unless a touch handler is bound, and Android Chrome delays it to disambiguate
from a scroll. `pointerdown` fires immediately on both. `pointercancel` is what
makes a scroll-that-started-on-a-tile correctly *not* look like a press.

Requires `touch-action: manipulation` on the tile (kills double-tap-zoom delay) and
`user-select: none` + `-webkit-touch-callout: none` (kills the long-press
selection/callout menu, which is pure noise on a control surface).

`navigator.vibrate(10)` fires here, feature-detected. **Android only** — iOS has no
Vibration API and Apple has shown no intent to add one. This is acceptable
degradation precisely because, unlike a physical deck key, the tile is under the
user's finger *and in their field of view*: the visual press state is the primary
confirmation on both platforms, and haptics is a bonus on one.

**Layer 2 — Pending (0 ms → resolution). Optimistic, and visibly so.**
The cyan active ring moves to the tapped tile immediately, **rendered dashed and
slowly pulsing** rather than solid.

**Layer 3 — Reconcile.**
On 2xx: ring goes solid; the next `/api/view` poll confirms independently.
On error or a 2.5 s timeout: ring snaps back to the true active tile, the tapped
tile flashes an error state (~600 ms), and the header shows a transient failure
line. **No dialog** — a failed session switch is recoverable by tapping again.

### 6.3 "Does it lie if the request fails?"

**A plain optimistic highlight does. This one does not — because the optimistic
state is visually distinct from the confirmed state.** A dashed pulsing ring is not
a claim that the switch happened; it is a claim that it is *in flight*. The user can
tell those apart at a glance, and when the rollback in Layer 3 fires it is a
resolution rather than a contradiction.

**This is a deliberate refinement of the sidecar's pattern, not a copy of it.**
`muxplex-deck/AGENTS.md` mandates "optimistic repaint, never block," and the
physical deck's repaint is *unqualified* — the key just lights up. That is not
sloppiness; a 72 px key rendered by PIL has no room for a legible third state, so
the sidecar spends its two available states on the two that matter and accepts the
brief lie. **A phone has the pixels to afford a third state, so it should spend
them.** Same rule, better medium — exactly the argument `SOFT_DECK_DESIGN.md` §4
makes about what ports and what gets re-derived.

Reconciliation still comes from `/api/view`, never from the local optimistic
value — the client must not become a second source of truth for `active_session`.

### 6.4 Scroll hygiene

`overscroll-behavior-y: contain` on the scroll container.

This suppresses Android Chrome's native pull-to-refresh, and that suppression is
the point: an accidental over-scroll on a propped device would trigger a **full
page reload**, which drops the wake lock, re-runs auth, and on iOS risks the
§4.4 scope-ejection path. A reload is a strictly worse outcome than any refresh it
could deliver.

**Rejected: pull-to-refresh as `refresh_now`** (proposed in `SOFT_DECK_DESIGN.md`
§4.4). The page already polls every 2 s. A manual refresh gesture on a
2-second-polling surface is ceremony with a hazard attached; if the poll is broken,
the connection indicator says so, and pulling on a dead poll will not fix it. This
is a substantive disagreement with the architecture doc and it is the simpler
answer.

---

## 7. What I rejected, and why

| Rejected | Why |
|---|---|
| **Width-based breakpoint classes for the grid** | `auto-fill` + `minmax()` answers the only question width poses, intrinsically. Five media queries collapse to zero, and the derived tile-width band (136–203 px) is *tighter* than any hand-tuned breakpoint set would produce. |
| **Porting the PWA's four grid breakpoints** (`style.css:359–393`) | Correct for a terminal-preview app where a tile is a document; wrong for a control surface where a tile is a button. The single-column collapse below 599 px is the specific thing to avoid — it destroys glanceability. |
| **Fluid typography (`clamp()`, `vw` units, container queries)** | The intrinsic grid already normalised tile width to 1.5×. Fluid type has nothing left to do. `vw`-based type is actively wrong here: at 1024 px wide a `vw` size would balloon while the tile stays 137 px. |
| **Locking orientation via `manifest.orientation: "portrait"`** | Honoured on Android, unsupported on iOS (MDN: not Baseline). Buys divergent behaviour on a feature that only helps if universal. |
| **Locking via `screen.orientation.lock()`** | Not implemented in Safari on any platform; on Chrome Android the spec requires fullscreen as a pre-lock condition. Would mean adopting fullscreen display mode purely to win a lock we still couldn't ship on iOS. |
| **`user-scalable=no` / `maximum-scale=1`** (as the existing app does) | Ignored by iOS Safari since iOS 10 → divergent behaviour for zero benefit; a WCAG 1.4.4 failure; and it doesn't address the real hazard, which `touch-action: manipulation` handles precisely. |
| **A confirmation step or long-press-to-activate on tiles** | Converts a one-tap product into a two-tap product to defend against an occasional error. The `toggle_last` chip is a one-tap undo that already had to exist. |
| **Auto-dimming the UI after idle to save battery** | Optimises against the surface's only purpose. OS brightness is the user's control and it works. |
| **A `NoSleep.js`-style looping-video wake-lock fallback** | A hack against a WebKit bug Apple fixed in 18.4. Burns battery and decode hardware continuously — the opposite of the goal — and becomes dead code on device update. Honest degradation (`asleep — tap to retry`) is better. |
| **A wake-lock preference in a settings sheet** | Same information, more surface. One header element that *reports truth and toggles* satisfies MDN's guidance and cannot drift out of sync with the sentinel. |
| **Pull-to-refresh** | Ceremony on a 2 s-polling surface, and the gesture it rides on (over-scroll) is one we specifically want to suppress because it can trigger a full reload. |
| **Unqualified optimistic highlight (the sidecar's exact pattern)** | On hardware it's forced by 72 px. On a phone it's a lie we have the pixels to avoid. Dashed-pulsing pending state costs nothing and removes the dishonesty. |
| **A service worker for offline/caching** | Already rejected architecturally (`SOFT_DECK_DESIGN.md` §11) and rejected again on responsive grounds: the deck's whole state is one fetch away, and `Cache-Control: no-cache` on this frontend is documented as load-bearing. |
| **Virtualised / windowed tile rendering** | 30 tiles. Nothing to virtualise. |
| **Thumb-zone-driven layout** (bottom-heavy primary actions) | Standard mobile ergonomics, wrong premise here. The device is propped and reached over, not held one-handed (`SOFT_DECK_DESIGN.md` assumption 3). Optimise for target size and legibility, not for thumb arc. |
| **Separate tablet and phone layouts** | Device-class thinking. The capability questions are "how many legible tiles fit across" (intrinsic) and "is there room for a preview band" (one height query). Neither maps to a device name. |

---

## 8. Open items for hardware verification

Reasoned, not measured — the same honesty `KEY_DESIGN_SYSTEM.md` §5 applies to the
physical deck. Each has a stated fallback.

1. **136 px `--tile-min`.** Derived from a ~16-character name budget at 14 px. If
   names truncate too often in practice, raise to 150 px (holds 2 columns up to
   W=608) and lose a column on tablets.
2. **520 px Short-class threshold.** Chosen so phones-in-landscape hit Short and
   tablets never do. If a small tablet in landscape (744 px tall) turns out to want
   Short anyway, the threshold moves — it is one number.
3. **4:3 tile aspect.** Balances preview lines against visible tile count. Squarer
   (1:1) gives more preview and fewer tiles; the lever is one custom property.
4. **72 px Short-class tile height.** 1.5× the touch floor. Verify the name is
   still legible peripherally with the preview gone.
5. **2.5 s optimistic-pending timeout.** Should be comfortably above tailnet
   round-trip and comfortably below the point where the user re-taps. Untested.
6. **iOS version on the target device.** If below 18.4, wake lock in the installed
   PWA is broken (§3.3) and this will look like our bug. Check first.

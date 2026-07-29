# Soft Deck — Layout & Information Architecture

**Status:** DESIGN ONLY. No implementation code. Consumes the architecture decision in
`muxplex-deck/docs/SOFT_DECK_DESIGN.md` (same-origin `/deck/` route, PWA, no native app)
and answers the layout questions that document deferred to §12 and Open Question 2.

**Scope:** v1 = switch session + change view. No create, no delete, no terminal.

> **Path note.** This file lives at `muxplex/frontend/deck/` in package-relative terms,
> which on disk is `muxplex/muxplex/frontend/deck/`. `main.py:2748` mounts
> `_NoCacheStaticFiles(directory=_FRONTEND_DIR, html=True)` at `/`, so `deck/index.html`
> is served at `/deck/` with zero Python.

---

## 0. The one-sentence brief this document is designed against

> A laptop is doing the work. A phone or tablet sits beside it, screen on. The user
> glances over for **about one second**, taps, and the laptop switches. The phone is
> **peripheral** — attention lives on the laptop, and the tap often happens mid-keystroke.

Every decision below is scored against that sentence. The two properties that follow
from it, and that nothing may trade away:

1. **A tile must be hittable without being read.** Position is the primary index; the
   label is confirmation, not navigation.
2. **The surface must never lie.** A propped phone that quietly lost the server and is
   showing a confident, stale grid is worse than a phone showing nothing. This is the
   same failure class that produced five incidents this month and the v0.9.5 regression
   net; it must be designed for here, not discovered here.

---

## 1. Page skeleton

Three regions. Two are sticky; one scrolls. There is nothing at the bottom of the screen.

```
┌────────────────────────────────────────────────┐
│ ● ▾ agents                    ⇄ scratch        │  HEADER   52px, sticky
├────────────────────────────────────────────────┤
│  amplifier-main    deckwork                    │  ATTENTION  44px, sticky,
├────────────────────────────────────────────────┤             conditional (§1.2)
│ ┌──────────────┐ ┌──────────────┐              │
│ │amplifier-main│ │ deckwork     │              │  GRID     scrolls
│ │──────────────│ │──────────────│              │
│ │ $ pytest -q  │ │ Waiting for  │              │
│ │ 906 passed   │ │              │              │
│ │          2m  │ │         14m  │              │
│ └──────────────┘ └──────────────┘              │
│ ┌──────────────┐ ┌──────────────┐              │
│ │ muxplex      │ │ scratch      │              │
│ ...                                            │
└────────────────────────────────────────────────┘
```

### 1.1 Header — 52px, sticky, always present

Three things, left to right. Nothing else.

| Element | Content | Why it earns its place |
|---|---|---|
| **Liveness dot** | 8px dot, `--accent` when fresh, `--warn` when stale, `--err` when the last poll failed | Property 2. Costs 20px. See §6. |
| **View button** | `▾ ` + current view name, 15px/600 | The view is the *frame* for everything below. If you can't see which view you're in, every tile below is ambiguous. Tap opens the view sheet (§4). |
| **Toggle-last chip** | `⇄ ` + **the previous session's name**, right-aligned, truncating | Ports `toggle_last` from the physical deck (`KEY_DESIGN_SYSTEM.md` §6.2). The deck had to render `LAST` because a name didn't fit at 72px; here it does, so show it. A chip that says `LAST` makes you think; a chip that says `scratch` doesn't. |

**No hostname.** Install-constant, not state — the same argument that removed it from the
physical deck's view key. **No title, no logo, no settings gear, no session count.**

**No bottom bar of any kind.** The phone is *propped*, not held
(`SOFT_DECK_DESIGN.md` assumption 3), so thumb-zone reasoning does not apply — and the
bottom edge of a propped tablet is the part nearest the desk lip and hardest to reach
cleanly while typing. Frequent actions (tap a tile) are everywhere; the rare action
(change view) can afford a longer reach to the header. That is the correct Fitts's-law
allocation, and it is the opposite of what a phone app normally does.

### 1.2 Attention strip — 44px, sticky, conditional on *overflow*, not on attention

A horizontal row of amber chips, one per session with `needs_attention`. Tap = switch.

The interesting decision is **when it exists**, and the obvious answer is wrong.

- **Wrong:** render it whenever any session needs attention. That inserts 44px and pushes
  every tile down *at exactly the moment you are reaching over to tap one*. It breaks
  spatial constancy precisely when constancy matters most, in service of a signal the
  amber tile band already carries.
- **Right:** the strip's only real job is *"tell me what's shouting when the shouter is
  scrolled off-screen."* If every tile is visible, the strip is pure duplication.

**Rule: the strip's 44px band is reserved when — and only when — the grid overflows.**
Chips render into the reserved band when attention exists; the band sits empty when it
doesn't. A bell firing therefore never moves a tile. Overflow is one measurement per
render (`grid.scrollHeight > grid.clientHeight`) toggling one class on the root; the
reflow only happens when that boolean flips, which is rare.

*Fallback if the overflow measurement proves fiddly: always reserve the band. Costs 44px
permanently and, at the tightest viewport (landscape phone), one grid row. Acceptable but
strictly worse.*

The strip **never scrolls the grid** and the grid **never auto-scrolls**. Nothing on this
page moves except in response to a human finger.

### 1.3 Grid — scrolls, and this is the honest part

See §3 for the density numbers and §2 for why it's a grid.

### 1.4 The four non-tile states

Every one of these must be designed or the surface will lie.

| State | Presentation |
|---|---|
| **Loading** (first paint, no data yet) | Grid renders skeleton tiles at the last known count, or one centred `…` if unknown. Never an empty grid — an empty grid is a *claim*. |
| **Empty view** (view resolved, zero sessions) | Centred, `--text-muted`: the view name + "no sessions". This is a real answer, not an error. |
| **Unknown view** (`/api/view` echoes an unresolvable `active_view`) | Same as empty, but the header view name renders in `--warn`. Matches the server's documented behaviour of echoing the name with empty sessions. |
| **Stale / disconnected** | §6. The whole grid desaturates. Non-negotiable. |

---

## 2. Grid or list

**Grid.** A stable-order, uniform-tile grid — and the deciding argument is not "decks are
grids."

### 2.1 The real argument

A list is genuinely competitive, and the strongest evidence for it is *in this codebase*:
`style.css:855–889` already collapses the PWA's session grid to a three-tier list below
599px, with variable row heights ordered by attention. That was a considered decision, and
it is correct — **for the PWA's job**. The PWA's phone job is *reading*: browse sessions,
pick one, look at it. Scanning names and previews is what a list is for. It is also,
precisely, an attention-ordered variable-height list — the exact opposite of constancy —
and that is fine there because the PWA does not promise constancy.

The deck's job is different: **hit a target you already chose before you looked.** For
that task:

1. **A list's targets are wide in X and short in Y — and Y is the axis that scrolls.**
   In a list, the coordinate you must remember is the coordinate that moves. In a grid,
   the *column* index is scroll-invariant, so even after scrolling you retain half your
   spatial memory. This is the load-bearing argument.
2. **A grid fits roughly twice as many items per screen at a given legible tile size**
   (§3), so it reaches the scroll threshold half as often. Since scroll is what destroys
   constancy, the layout that delays scroll wins by that alone.
3. **2D position is a stronger memory cue than ordinal position.** "Bottom-left" is a
   landmark; "seventh from the top" is a count.
4. Fitts's law favours the list on target *width* — a full-width row is an easier hit than
   a 168px tile. But a 168 × 128px tile is ~3× the 44px touch minimum in *both* axes; the
   list's extra width is buying accuracy that is already bought.

### 2.2 What would make me wrong

Two facts about the user's actual sessions, neither of which I can derive:

- **Names routinely exceeding ~16 characters.** At the density in §3 a tile holds ~16
  characters of session name before ellipsis. Chronic truncation makes tiles unreadable
  and the grid collapses to "unlabelled coloured boxes" — at which point the list, which
  never truncates, wins. *Weak evidence in favour of the grid:* the names visible across
  this project's docs and mockups — `amplifier-main` (14), `deckwork` (8), `muxplex` (7),
  `scratch` (7) — all fit. That is a sample from documentation, not from the live server.
- **Typical views holding more than ~24 sessions.** Past that you are always scrolling,
  constancy is already lost, and the list's higher information density is the better
  trade. `SOFT_DECK_DESIGN.md` assumption 4 says 5–30, which straddles the line.

### 2.3 The graceful degradation nobody has to build

At viewport widths below ~352px the intrinsic sizing in §3 yields **one column**, and a
one-column grid of full-width tiles *is* a list. The fallback is the same code. No
breakpoint, no second layout, no decision.

### 2.4 Sort order

**`sort` omitted — server enumeration order.** Explicitly *not* `attention`.

`GET /api/view`'s `attention` sort reorders tiles as bells fire. On hardware you look at
directly that is tolerable; on a surface you want to hit without looking it is hostile.
The soft deck inverts the sidecar's default. Attention is expressed as a **decoration on
the tile** and, when it would otherwise be off-screen, as a **chip in the strip** — never
as a reorder. This confirms `SOFT_DECK_DESIGN.md` §4.3.

Corollary: order is stable across polls, so a tile's position changes only when a session
is created or destroyed. That is the strongest constancy a reflowing surface can offer.

---

## 3. Density

### 3.1 The unit question, settled once

**Reason in CSS pixels and stop.** The CSS reference pixel is defined as an angular unit —
the visual angle of 1/96 inch at arm's length — and modern phones and tablets honour it
closely enough for design purposes at their natural viewing distances:

| Device class | Typical CSS px per physical inch | At its natural distance, 1 arcmin ≈ |
|---|---|---|
| Phone (390 CSS px over ~2.7 in), ~400mm | ~144 | 0.66 CSS px |
| Tablet (1194 CSS px over ~9.7 in), ~450mm | ~123 | 0.63 CSS px |

The two converge within 5%. **A 17px label subtends ~17 arcminutes on both** — which is
almost exactly the angular size `KEY_DESIGN_SYSTEM.md` §2 measured for the physical deck's
PRIMARY and judged "comfortable for a session name you are hunting for."

This is why there are no device breakpoints in this document. There is nothing to vary.

### 3.2 The tile floor

| Token | Value | Derivation |
|---|---|---|
| Minimum tile width | **164px** | 14-char name at 17px/600 (~9.4px avg advance) = 132px + 2×12px padding = 156px, rounded up. Sits just under the 168px ceiling above which a 360px viewport drops to one column — so the narrowest common phone still gets a grid, with 4px of slack. |
| Minimum tile height | **128px** | 40px NAME band + 88px preview field. |
| Gap | **8px** | |
| Page padding | **8px** | plus `env(safe-area-inset-*)`. |

That is the whole layout system:

```
grid-template-columns: repeat(auto-fill, minmax(164px, 1fr));
```

One declaration. No breakpoint tokens, no column tables, no orientation queries. Tiles
grow to fill; the column count falls out of the viewport.

**Touch:** 164 × 128 is ~3.7× the WCAG 2.5.5 / Apple HIG 44px minimum on the short axis
and 2.7× Material's 48dp. Deliberately far above minimum, because the user is not looking
carefully — they are typing on a laptop and reaching over. Minimum-sized targets assume
attention that this surface explicitly does not have.

### 3.3 What that yields

`columns = floor((V_w − 8) / 172)` · `rows = floor((V_h − 52 − strip − 20 + 8) / 136)`

| Viewport (CSS px) | Class | Cols × Rows | **Tiles at rest** | Tile width |
|---|---|---|---|---|
| 360 × 780 | phone portrait, narrow | 2 × 4 | **8** (10 without strip) | 168px |
| 390 × 844 | phone portrait | 2 × 5 | **10** | 183px |
| 430 × 932 | large phone portrait | 2 × 6 | **12** | 203px |
| 844 × 390 | **phone landscape — the worst case** | 4 × 2 | **8** | 201px |
| 600 × 960 | small tablet portrait | 3 × 6 | **18** | 189px |
| 744 × 1133 | tablet portrait | 4 × 7 | **28** | 176px |
| 1024 × 768 | tablet landscape | 5 × 4 | **20** (25 without strip) | 195px |
| 1194 × 834 | 11" tablet landscape | 6 × 5 | **30** | 190px |

Rows assume the attention strip is *present* — the pessimistic case, which per §1.2 only
occurs when the grid already overflows. Two configurations lose a row to it (noted inline);
everywhere else the strip is free, because 44px happens to fall inside the rounding slack.

**Read the table this way:** against `SOFT_DECK_DESIGN.md`'s 5–30 session assumption, a
tablet in either orientation shows the entire working set with no scroll at all. A phone in
portrait covers a 10-session view. Phone landscape is the only configuration that scrolls
routinely, and it is also the configuration nobody props up.

### 3.4 Type and character budgets

| Role | Size | Ink | Notes |
|---|---|---|---|
| **PRIMARY** — session name | 17px / 600 | `--text` `#F0F6FF` | ~16 chars at the 164px floor, ~20 at 200px. Ellipsis, single line. |
| **SECONDARY** — state, chips, view name | 12–15px / 500 | `--text-muted` `#8E95A3` | |
| **PREVIEW** — terminal snapshot | 12px mono / 400 | `--text-dim`-ish, ≈`#7A7A7A` | ~20 columns × ~5 lines at the floor. |

The physical deck's ~7-character budget is gone; so is its 16/11/11 scale and its `f(S)`
arithmetic. Nothing about the 72px key's type system survives the medium change — only its
*rules* do (§5).

**One honest correction to `SOFT_DECK_DESIGN.md` §4.2.** It says the preview "graduates
from texture to content" on a phone. Half true. At 12px mono in a 164px tile you get ~20
columns and ~5 lines — enough to read `$ pytest -q` or `906 passed` or `Waiting for…`, and
nowhere near enough to read prose. The accurate label is **recognisable phrases**: between
texture and content. Naming it precisely matters, because "it's content now" is an
invitation for someone to later shrink it and add columns, and that would land it back
below the letter-identification threshold with none of the honesty the deck system had
about it.

---

## 4. Scroll — and whether a deck may have it

**Scroll exists so nothing is unreachable. It must never be how you reach the thing you
normally reach.** That distinction is the whole answer.

The glance-and-tap contract only holds for tiles on screen *at rest*. So scroll does not
break the contract — it defines the contract's boundary. The design's job is to make the
boundary wide enough that you rarely meet it, and to make meeting it non-destructive:

- **The mechanism that keeps you inside the boundary is the *view*, not the scrollbar.**
  muxplex already has views: a server-side content filter. If your working set doesn't fit
  on the propped screen, the correct fix is to make a view, not to scroll further. This is
  the phone's analogue of what paging was on the physical deck — except views are semantic
  and pages were arithmetic, which is why `page_*` dies here (`SOFT_DECK_DESIGN.md` §4.2)
  and views do not.
- **Header and attention strip are sticky.** The two questions you can ask without
  scrolling are the two that matter: *which view am I in* and *what is shouting*.
- **Scroll resets to top on view change.** A new view is a new coordinate space.
- **Nothing auto-scrolls, ever** — not on bell, not on poll, not on active-session change.
  An auto-scroll would relocate every tile in response to an event the user did not cause,
  which is the single most destructive thing that could happen to this surface.
- **Scroll position survives a poll.** Re-render must be a diff against stable keys, not a
  rebuild, or the list jumps under the finger.

---

## 5. The view dimension

### 5.1 Separate identity from selection — the deck couldn't, the phone can

The physical deck fuses them into one key (`view_picker`: NAME `VIEW`, BODY = current view
name) and then blows the whole surface away to show a picker, because it has nowhere else
to put it. On a phone these are two different needs with two different frequencies:

- **View identity** is read constantly — every glance, implicitly, because it frames every
  tile. It must be **free and always present**: the header button (§1.1).
- **View selection** is rare. It gets an on-demand surface.

### 5.2 The surface: a bottom sheet, and the extra room buys recognition

Tapping the header view button opens a sheet listing the views from `/api/view`'s `views`
array (`"all"` + user views, in settings order; `"hidden"` deliberately excluded by the
server). Tap a row → `PATCH /api/state` → sheet dismisses → grid re-renders → scroll resets.

Each row carries **the view name, its session count, and an attention badge**. That is the
one thing the extra space genuinely buys: choosing a view stops being recall ("which one
had the build in it?") and becomes recognition. The 72px key could never do this.

Sheet rows are 56px, full width. Dismiss on backdrop tap, on Escape, and on selection.

### 5.3 Rejected view mechanisms

| Rejected | Why |
|---|---|
| **Persistent view chip row** (always-visible segmented control) | Zero-tap awareness sounds right, but it spends the *scarcest* resource — vertical space, ~44px, one third of a tile row — on the *rarest* action. And it degrades badly: unbounded view count means it either scrolls horizontally (hiding options, and a horizontal scroller is a poor blind target) or wraps (eating more rows). The header button already delivers the identity half for free. |
| **Horizontal swipe between views** | The hostile failure mode: a propped phone gets brushed while you reach past it, the view silently changes, and you glance over at a grid of sessions you did not ask for with no indication why. Also collides with vertical scroll on diagonal gestures, and is undiscoverable. Gesture-only navigation on an unattended surface is a bad trade at any price. |
| **Persistent sidebar of views on wide viewports** | Genuinely fits — a 160px rail costs ~13% of an 1194px viewport and still leaves 5 columns. Rejected on ruthless simplicity: it is a second layout and a second code path for a function the sheet already performs identically at every size. One mechanism, all viewports. |
| **Porting `view_prev` / `view_next` as on-screen arrows** | Two permanent targets to step blindly through an ordered list, when the sheet shows the whole list and lets you jump. Prev/next exist on hardware because a fixed key grid cannot render a list. |

---

## 6. Staleness — the state the physical deck never needed

The sidecar is wired to the machine and has `DEVICE_ABSENT` for the inverse case. A propped
phone on a tailnet has a failure mode the physical deck does not: **the server goes away and
the last-good render stays on screen, looking exactly like the truth.** That is the "stale
state reported as current" class this project already has five incidents and a regression
suite for. It is a layout concern because the remedy is visual and it must be impossible to
miss.

| Age of last successful poll | Presentation |
|---|---|
| < ~6s (≈3 poll cycles at 2s) | Normal. Liveness dot `--accent`. |
| ~6–30s | Liveness dot `--warn`, header appends the age (`18s`). |
| > ~30s, or last request errored | **Whole grid desaturates** (`filter: grayscale(.7) opacity(.45)`), dot `--err`, header shows the age. Tiles remain tappable — an optimistic tap that then fails is more useful than a dead surface — but nothing on screen can be mistaken for live. |

Polling stops entirely on `visibilitychange` to hidden, and on return the surface enters the
stale presentation until the first successful poll lands. A backgrounded deck must not drain
battery, and a foregrounded deck must not show what it remembered from an hour ago.

---

## 7. The tile

Layout only. The visual system is `KEY_DESIGN_SYSTEM.md`; what follows is what survives the
medium change.

```
┌──────────────────────────┐  ← outline: 3px --accent when active,
│ amplifier-main           │     outline-offset:-3px → ZERO layout cost
├──────────────────────────┤  NAME  40px, opaque scrim, PRIMARY 17/600
│ $ pytest -q              │     amber fill + #000 ink when needs_attention
│ 906 passed               │  BODY  preview field, bottom-anchored,
│                     2m   │  STATE composited over the field, bottom-right
└──────────────────────────┘
```

- **Zone model survives, with one honest amendment.** NAME is flow height; BODY is the
  preview field filling the remainder; **STATE is a reserved *overlay region*, not reserved
  flow height.** The invariant that actually matters — every tile puts its state in the same
  place — holds. Reserving STATE as flow height instead would cost 20px × 10 tiles = 200px of
  vertical on a phone, which is the scarce resource. A right-side gradient scrim keeps the
  state legible over the preview's last line.
- **Exactly one PRIMARY per tile.** The name. Ports verbatim.
- **Two orthogonal state channels, ports verbatim** — and CSS implements them better than PIL
  did. `active` = cyan `outline` with negative offset: drawn outside the border box, costs
  exactly zero content pixels, which is what `KEY_DESIGN_SYSTEM.md` §3 wanted and had to
  achieve by arithmetic. `needs_attention` = NAME band fills `--bell` `#F1A640` with ink
  inverted to `#000000` (measured 10.4:1). Both can be present without touching.
- **No state by hue alone.** Ring present/absent is a shape channel; band fill + ink polarity
  is a value channel. Survives total colour loss.
- **Palette is already shared.** `style.css:1–52` defines `--accent: #00D9F5`, `--bell:
  #F1A640`, `--bg: #0D1117` — the same three values `KEY_DESIGN_SYSTEM.md` §4 lifted from it.
  The deck stylesheet reuses the tokens; it does not redefine them.
- **`:active` is new and load-bearing.** The physical deck has no hover, focus, or press
  state. Here, the tap must confirm *before* the network round-trip: on `pointerdown` the
  tapped tile takes the cyan outline and the previous holder loses it, immediately. That is
  the muxplex-deck "optimistic repaint, never block" rule expressed as CSS. `POST /connect`
  follows; the next poll reconciles; a failure snaps the outline back and raises a toast.
- **`prefers-reduced-motion`** suppresses the sheet slide and any tile transition. State
  changes remain instant regardless — they are information, not decoration.

---

## 8. Orientation

The grid is intrinsically sized, so the horizontal axis handles rotation with no code. What
is worth stating:

| Configuration | Behaviour |
|---|---|
| **Tablet, either orientation** | Nothing changes. `auto-fill` yields more columns in landscape. Both fit the whole working set (§3.3). |
| **Phone portrait** | The design centre. 2 columns; 5 rows at ≥844px tall (strip free), 4 rows on a shorter 780px viewport when the strip is up. |
| **Phone landscape** | The only tight case: header + strip = 96px of a ~390px viewport, 25%. Verified to still yield 2 rows (390 − 52 − 44 − 20 = 274px ≥ 2 × 136 = 272). No special case needed — but the margin is 2px, so this is item 5 in §10. |

**No orientation media queries.** No `@media (orientation: …)` anywhere. Orientation is not
a capability; viewport size is, and the intrinsic grid already reads it.

**Manifest stays `"orientation": "any"`.** A locked orientation would serve constancy — reflow
on rotation is the one place the phone genuinely cannot match the physical deck, since column
count changes and every tile moves — but the manifest cannot be capability-conditional, and a
tablet wants landscape while a phone wants portrait. The honest position: **constancy holds
within an orientation.** A propped device is not rotated in practice, so this is a theoretical
cost, and paying for it with a wrong default on half the hardware would be worse.

**`viewport-fit=cover` + `env(safe-area-inset-*)` on the page padding.** A phone propped in
landscape has its notch on a long edge, directly into the first or last grid column.

---

## 9. What I rejected, and why

| Rejected | Why |
|---|---|
| **Attention-ordered layout** (`?sort=attention`) | Reorders tiles as bells fire, destroying the spatial constancy that is the entire reason to prop a screen next to a laptop. Attention becomes a tile decoration plus an overflow-conditional strip. |
| **Attention strip conditional on attention** | Inserts 44px and shifts every tile down at exactly the moment you are reaching to tap one. Made conditional on *grid overflow* instead — the strip's real job is reaching what's scrolled away, and when nothing is scrolled away the amber tile band already says everything. |
| **A list layout** | Genuinely competitive and already present in this codebase for the PWA's phone view — but the PWA's job is reading and the deck's is blind targeting. A list puts the coordinate you must remember on the axis that scrolls, and fits ~half as many items per screen, so it hits scroll twice as often. §2. Remains the fallback if names routinely exceed ~16 chars or views routinely exceed ~24 sessions. |
| **Breakpoint tokens / a size-class system** | `repeat(auto-fill, minmax(164px, 1fr))` produces every row in the §3.3 table from one declaration. A breakpoint system here would be tokens nobody will ever vary — dead weight, and exactly what `KEY_DESIGN_SYSTEM.md` §7 rejected for the same reason. |
| **Any `@media (orientation: …)` rule** | Orientation is not a capability. Viewport size is, and the intrinsic grid already reads it. Device-shaped rules are the ecosystem's central architectural prohibition. |
| **A bottom tab/action bar** | The phone is propped, not held, so thumb-zone reasoning does not transfer; the bottom edge of a propped device is the worst reach, not the best. And v1 has exactly two verbs, one of which is the entire grid. |
| **Horizontal swipe to change view** | Silent state change on a surface that gets brushed while you reach past it, colliding with vertical scroll, undiscoverable. §5.3. |
| **Persistent view chip row** | Spends the scarcest resource (vertical space) on the rarest action, and degrades badly as view count grows. §5.3. |
| **Sidebar of views on wide viewports** | Fits comfortably, and is still a second layout and a second code path for something the sheet does identically everywhere. |
| **Porting the 72px type scale, the ~7-char budget, or the `f(S)` formulas** | Derived from an arcminute budget on an 18mm square at 600mm. Wrong by an order of magnitude here, and CSS has intrinsic sizing so the arithmetic has no reason to exist. §3.1, §3.4. |
| **Porting `page_*` and the `key.N` binding model** | Paging is a workaround for a fixed key count; there is no `key.7` on a reflowing surface. Building a binding table for a phone would be cargo-culting the deck's *constraints* as if they were its *design*. Confirms `SOFT_DECK_DESIGN.md` §4.4. |
| **Reserving STATE as flow height** (strict zone model) | Costs ~200px of vertical on a phone — a whole tile row and a half — to enforce an invariant that an overlay region preserves anyway. The rule that matters is "state is always in the same place," not "state occupies its own rows." §7. |
| **A denser preview** (smaller font, more columns) | Would push glyphs back below the letter-identification threshold, i.e. back to pure texture, losing the single biggest capability gain of this medium. The preview is deliberately at the low end of readable, not the high end of dense. |
| **Variable tile heights by tier** (the PWA's mobile pattern) | Attention-driven sizing means tile geometry changes as bells fire — the same constancy violation as attention-driven ordering, in the other axis. Uniform tiles, always. |
| **Auto-scrolling to a newly-shouting session** | Relocates every tile in response to an event the user did not cause. The most destructive single thing that could happen to this surface. |
| **A skeleton-free first paint** | An empty grid is a *claim* that the view is empty. Skeletons, or nothing — never a confident lie. §1.4. |

---

## 10. What cannot be settled without the real device

Each has a named fallback, so nothing blocks. Items 3 and 4 are deliberate mirrors of
`KEY_DESIGN_SYSTEM.md` §8 items 1 and 2 — the same two questions, re-asked on an entirely
different panel, and the hardware answers do not transfer.

| # | Question | Fallback |
|---|---|---|
| 1 | **Is a 164px tile at 2 columns comfortable on a 360–390px phone, or does it read as cramped?** This is the empirical crux of the grid-vs-list decision. If cramped, the honest move is 1 column — which is the list, arrived at by adjusting one number. | Raise the floor to ~200px → 1 column below 416px. |
| 2 | **Is 12px monospace preview readable at 30–50cm, or is it still texture?** Determines whether §3.4's "recognisable phrases" claim holds or whether the deck's honest TEXTURE label should have carried over after all. | 13px mono, accepting ~18 columns; or drop the preview to 3 lines and raise the name. |
| 3 | **Does a 3px cyan outline read as "active" in peripheral vision on a bright phone panel?** | 4px, or add a NAME-band tint. `outline-offset` means thickness is free — zero layout cost at any width. |
| 4 | **Does `#F1A640` bloom and smear the black NAME-band ink on an OLED phone?** Different panel technology from the deck's LCD; the deck's answer does not transfer. | `#C97F1E` band with black ink, per the deck system's own fallback. |
| 5 | **Phone landscape has a 2px margin for its second grid row.** Any change to header or strip height eliminates it. | Merge the liveness dot into the view button and drop the header to 48px, buying 4px. |
| 6 | **Does the overflow-conditional attention strip feel right, or does its appear/disappear on session-count change read as jumpy?** | Always reserve the band; costs one row in landscape phone. |
| 7 | **Is the sticky header worth 52px, or does a propped device want maximum grid?** | Make the header scroll away and the strip stay sticky — but this loses always-visible view identity, which §5.1 argues is load-bearing. I would not do it. |
| 8 | **Does the whole thing actually fit the working set?** Everything in §3.3 assumes 5–30 sessions. One look at the real view answers it, and it is the only measurement that can invalidate the grid decision outright. |

**Cheapest way to settle 1, 2, 3 and 5:** serve the static page from the existing frontend
mount and open it on the phone over the tailnet. No build step, no install, no deployment —
the whole surface is one HTML file. Look at it propped where it will actually live, at the
distance it will actually be, while typing on the laptop. That last condition is the test;
everything above is a prediction about it.

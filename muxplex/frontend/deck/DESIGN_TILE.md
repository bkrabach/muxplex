# Session Tile — soft deck

The single repeated component of `/deck/`. A phone or small tablet propped beside
the laptop; one tile per session in the current view; tapping one repoints the
laptop.

**Status:** DESIGN ONLY. No implementation. Companion to
`muxplex-deck/docs/SOFT_DECK_DESIGN.md` (architecture) and
`muxplex-deck/docs/KEY_DESIGN_SYSTEM.md` (the physical 72px key, whose rules this
inherits and whose numbers it does not).

> **Path note.** The brief named `muxplex/frontend/deck/`. In the repo that is
> `muxplex/muxplex/frontend/deck/` (the package dir), which is what
> `main.py:2748` mounts at `/`. This file is in the real one.

---

## 0. The medium, and what changed from the key

`KEY_DESIGN_SYSTEM.md §0` opens with a medium table because normal UI instinct is
wrong at 18mm. The same table, re-derived, is why most of its *numbers* die here.

| Property | 72px Elgato key | Phone tile | Consequence |
|---|---|---|---|
| Element size | fixed 72px, hardware-set | **140–200px, my choice** | Size is a design variable, not a constraint. This is the single biggest difference and it changes the answer to long names (§5). |
| Separation | bezel between LCDs | 8px CSS gutter | A ring at the tile edge is near its neighbour, not floating in black. Ring must not grow. |
| Viewing distance | 500–750mm | 300–500mm | ~15px CSS ≈ 25 arcmin vs the key's PRIMARY at 16. **Acuity stops being the binding constraint.** |
| Renderer | PIL: fill, TTF, stroke, crop | CSS | `clamp()`, wrapping, transitions, `:active`, `prefers-reduced-motion`. |
| Font weights | **one** (Aileron Regular) | the whole system stack | Hierarchy can use weight, which lets the size range stay narrow. |
| Interaction | physical press, felt | **touch, no hover, no felt press** | A pressed state must be *invented*. The key never needed one. |
| Latency | local render | network round-trip | Optimism and failure become design problems. The key had neither. |

Two of these are genuinely new problems (pressed state, failure) and one is a
genuinely new freedom (element size is mine). Everything else is the key system
with different numbers.

---

## 1. What the API actually returns

Verified against source, not assumed.

`GET /api/view` (`main.py:948`) — the canonical list. Server-resolved membership,
bell predicate and sort; `SOFT_DECK_DESIGN.md §7` requires the client never
re-derive these.

```
{ name, active, needs_attention, bell{last_fired_at, seen_at, unseen_count},
  last_activity_at }
```

`GET /api/sessions` (`main.py:847`) — adds `snapshot` (the pane text). `/api/view`
deliberately omits it to stay cheap for polling.

```
{ name, snapshot, bell, last_activity_at }
```

- `active` — server-global; **at most one session is active at a time.** This is
  an invariant the tile design leans on (§4.2).
- `needs_attention` — `bells.py:151`: `unseen_count > 0 and (seen_at is None or
  last_fired_at > seen_at)`. A resolved boolean. Do not recompute it.
- `last_activity_at` — unix epoch seconds, `#{window_activity}`, **may be absent**
  (`sessions.py:117`).
- `snapshot` — pane text, newest line last, from a ~2s shared poll cache.

Five fields. Four of them earn tile space; `bell.unseen_count` does not (§9).

---

## 2. Anatomy

The key system's zone model ports directly — three horizontal bands inside a
uniform margin, **reserved whether or not they hold ink**, with the preview as a
*field* underlaying the whole box rather than a string in a band
(`KEY_DESIGN_SYSTEM.md §1`).

```
┌────────────────────────────┐  ← ring: inset box-shadow, 0 layout cost
│  ┌──────────────────────┐  │     (the CSS analogue of "lives in the margin")
│  │ amplifier-           │  │  NAME   PRIMARY. Up to 2 lines. Opaque band.
│  │ provider-load        │  │         Height reserved for 2 lines always.
│  └──────────────────────┘  │
│                            │
│    ..7 passed              │  PREVIEW  TEXTURE. Absolutely positioned,
│    $ pytest -q             │           bottom-anchored, spans the whole box,
│    906 passed              │           painted *under* NAME and STATE.
│  ┌──────────────────────┐  │
│  │ 2m                   │  │  STATE  SECONDARY. Relative age, or FAILED.
│  └──────────────────────┘  │         Reserved even when the data is absent.
└────────────────────────────┘
```

**Read order is the same three questions as the key**, and that is the part worth
protecting:

1. **NAME** — *which session is this?* The decision. Exactly one PRIMARY per tile.
2. **PREVIEW** — *do I recognise it?* Reassurance, not information (§3).
3. **STATE** — *is it alive?* Ambient context you did not cause.

**Bottom-anchoring the preview is semantic, not aesthetic.** Terminal output's
newest line is last. Top-anchoring shows the oldest visible line — the least
informative one — and it is the mistake that looks fine in a mock and is useless
in practice.

### Geometry

| Token | Value | Basis |
|---|---|---|
| tile | `aspect-ratio: 1` | Square. See below. |
| grid min column | `8.75rem` (140px @ default root) | §5 — derived, not chosen. |
| `M` padding | `0.5rem` (8px) | `M = ring + gap`, ported from `KEY_DESIGN_SYSTEM.md §3`. Ring 3 + clear 3, with slack. |
| ring | `3px` inset | §4.1 |
| NAME reserve | `2 × 1.25 × 0.9375rem` ≈ 37px | Two PRIMARY lines, always. |
| STATE reserve | `1 × 1.25 × 0.6875rem` ≈ 14px | One SECONDARY line, always. |
| radius | `4px` | Matches the PWA. |

**Square, and why.** At 140px: NAME 37 + STATE 14 + padding 16 = 67, leaving ~73px
of preview ≈ 5 lines at 11px — pleasingly close to the physical deck's
hardware-verified 4 lines at 72px. At 200px it grows to ~9. Preview line count
rising with tile size is *correct*: a bigger tile means you chose more room, so
you get more context. Square also makes the grid regular at any column count with
zero extra rules, and it is one CSS declaration rather than a height scheme.

**Reserving both bands is worth more here than on the deck.** On separated LCDs,
raggedness is inferred by the eye. In a CSS grid the tiles are 8px apart and
misaligned bands are *obvious*. The anti-raggedness rule gets stronger, not
weaker.

---

## 3. The preview call: **keep it, and deliberately keep it as texture**

This is the load-bearing content decision, and it goes against
`SOFT_DECK_DESIGN.md §4.2`, which called the preview's graduation from texture to
readable content "the single biggest capability gain of the medium."

It is a real capability gain. **It is also a trap.**

**The argument.** At 72px the preview rendered ~4px glyphs — below
letter-identification threshold — so it *could* only be shape. On a phone it
can genuinely be read. But a control surface whose tiles invite reading has
become a monitoring dashboard: reading a terminal pane takes 3–8 seconds, not
one, and if you are reading the phone you are no longer using the laptop, which
is the exact thing `SOFT_DECK_DESIGN.md §1` says the deck is not for ("It is not
a terminal"). The medium *offers* readability. The use *declines it*.

**But cutting it entirely is worse.** A grid of dark rectangles with words in
them is a settings list. The preview's shape is a genuinely good recall cue —
"the one with the wall of green dots is the test runner" — and it is the reason
the physical session tile is the best-looking face in the whole system.

**Resolution.** Keep the preview as a field, at a size and contrast where it
reads as **shape at a glance and text on inspection**:

- 11px mono, `#7D8590` ink on `#0B0E14` ≈ **4.7:1** — passes WCAG AA, so I am not
  claiming a decorative exemption for text that is genuinely content, and I am
  not making it illegible on purpose. The hierarchy is carried by the *ratio* to
  the name (`#F0F6FF` ≈ 14:1, ~3× hotter), not by crippling the preview.
- `white-space: pre`, `overflow: hidden`, **never wrap.** A wrapped terminal
  snapshot is visual garbage — it destroys the very column structure that makes
  the shape recognisable.

This is the same correction `KEY_DESIGN_SYSTEM.md §4` made when it dropped preview
ink from `#A8A8A8` (8.6:1) to `#7A7A7A`: *the least important thing on the face
was the highest-contrast thing on the face.* The current PWA has that bug too —
`style.css:295` renders preview at `#c9d1d9` on `#000`, roughly **13:1**, hotter
than the session name. The soft deck must not inherit it.

**Write this down for the next maintainer:** the preview is dim *by choice*, not
by limitation. Someone will eventually try to "fix its legibility." There is
legibility there; we are declining to spend attention on it.

---

## 4. State without hue

`KEY_DESIGN_SYSTEM.md §3` split the two states onto orthogonal channels
specifically so neither depends on colour perception. **That ports verbatim, and
it is the best thing in the document.**

| State | Channel | Geometry | Survives total colour loss because |
|---|---|---|---|
| **Active** | shape — ring present/absent | the margin | a ring is there or it isn't |
| **Attention** | value — band fill + ink polarity inversion | the NAME band | light band with dark ink, in a field of dark tiles with light ink |
| **Pressed** | geometry + surface — scale + lighten | whole tile | momentary size change |
| **Failed** | **text** — the literal word, plus a ring | the STATE band | it says `FAILED` |

The two never collide because they never occupy the same place — the band is
inset by `M`, so the ring cannot cross it. Both can be present at once without
either being thinned. **Salience order is preserved: attention outranks active.**
A filled amber band beats a 3px ring, and that is right — attention is a summons,
active is a fact.

Greyscale check, done rather than asserted: `#F1A640` has relative luminance
≈0.47; the tile surface `#10131C` ≈0.007. Desaturated, the attention band is a
near-white bar with black text against near-black tiles with near-white text. It
is the most salient thing on the screen with the hue removed entirely. `#00D9F5`
≈0.55 — a bright ring on a dark tile. Neither is confused with the other because
they are in different places, and neither needs its hue.

### 4.1 The one change: the ring becomes an inset box-shadow

On hardware the ring lives in the bezel margin and costs zero content pixels. The
CSS with that same property is **`box-shadow: inset 0 0 0 3px var(--accent)`**:

- `border` changes the box unless `box-sizing` is right, and collides with the
  tile's own border.
- `outline` draws *outside* the element — into the 8px gutter, toward the
  neighbouring tile — and is reserved for focus (§4.4).
- `box-shadow: inset` draws inside, **costs zero layout**, never reflows text,
  and stacks with other shadows.

`M = 8px` against a 3px ring leaves a 5px clear gap. This is
`KEY_DESIGN_SYSTEM.md §3`'s `M = B + gap` rule, ported, with more slack because
CSS gives it away free. The design survives a 4px ring without changing anything
else, so ring thickness can be settled on the phone (§10).

3px rather than the key's 2: the ring is now adjacent to a neighbouring tile
across an 8px gutter rather than floating in bezel black, so it has less
figure-ground help.

### 4.2 The one-ring invariant

**At most one tile shows a cyan ring, ever.** The server guarantees exactly one
`active`. The optimistic path (§6) must therefore *remove* the ring from the
previously-active tile at the same moment it adds one — otherwise two rings
appear for one round-trip, and two rings is the "muddy target" failure
`KEY_DESIGN_SYSTEM.md §3` rejected, reappearing in a different costume.

### 4.3 Motion is an *additive* third channel, never the signal

CSS can pulse the attention band. The key could not. Use it — a slow, low-amplitude
opacity pulse on the band only — but the design must be **complete without it**:
it is off under `prefers-reduced-motion`, and a grid of pulsing tiles is noise, so
it stays subtle and stays on the band, not the tile.

### 4.4 Focus

`:focus-visible` cannot use cyan — it would read as active. A light `outline` with
`outline-offset: 2px`, drawn outward into the gutter. Different colour, different
geometry, different channel. The tile is a `<button>`, so this comes free.

---

## 5. Long names: **widen the tile, don't shrink the name**

Real names in this system: `amplifier-provider-load` (23), `muplex-stream-deck`
(18), `amplifier-main` (14). They **share prefixes** — which is what kills the
obvious answer.

**Front-loaded truncation is fatal here.** At 90px, `amplifier-provider-load` and
`amplifier-main` both become `amplifi…`. Ellipsis discards exactly the
discriminating tail. A tile that cannot tell two sessions apart has failed at its
only job, and it fails *silently* — it looks fine.

**The insight the medium hands us:** the key had to truncate because `S` was set
by hardware. **Here the column count is mine.** So do not design a truncation rule
that survives 90px — design a grid that never produces 90px.

**The rule, in three parts:**

1. **Wrap to a maximum of 2 lines.** Browsers already break after `-`, so
   `amplifier-provider-load` naturally becomes `amplifier-` / `provider-load`.
   Zero CSS for the good case. `overflow-wrap: break-word` as the safety net for a
   hypothetical unbroken name; `hyphens: none` so nothing inserts soft hyphens.
2. **Clamp at 2 lines with ellipsis** (`-webkit-line-clamp: 2`). The safety net,
   not the mechanism.
3. **Set the grid minimum so (2) almost never fires.**
   `grid-template-columns: repeat(auto-fill, minmax(8.75rem, 1fr))`.

**Where 8.75rem comes from** — derived, and it is the only number here I did
arithmetic for. At 15px system UI, mixed lowercase averages ~0.5em ≈ 7.5px/char.
Tile − 2×8px padding = content. 23 characters over 2 lines needs ~12 chars/line
→ ~90px content → **~106px tile is the true floor**. 140px is deliberate slack for
(a) longer future names and (b) OS text scaling, which on both platforms reaches
roughly +30% — at which point 15px→20px, the floor rises to ~135px, and 140 still
holds.

**The minimum is in `rem`, not `px`, and that matters.** A px minimum with rem
type means the grid does not grow when the user scales text, and the name
overflows precisely for the users who most need it not to. Both scale together or
neither does.

**On the "must work from ~90px" constraint — I am declining it, deliberately.**
The component *renders* correctly at 90px: nothing overflows, nothing overlaps,
the clamp catches it. But at 90px the name stops being identifiable given this
system's shared-prefix naming, so the *product* fails while the *component*
passes — the worst kind of failure. The grid minimum makes 90px unreachable by
accident. And the constraint is satisfied in practice anyway: 140px minimum on a
320px-wide phone (the narrowest that ships) with 12px page padding and an 8px
gutter leaves 288px → **2 columns of 140**. Portrait on a normal phone: 2–3.
Landscape: 4–5. Tablet: 5–6. **One fluid component, no size-class variants**, as
required — because the grid absorbs the variation instead of the tile.

Rejected alternatives are in §9.

---

## 6. Pressed and failed

The key never needed either: a physical press is *felt*, and a local render
cannot fail. Both are new, and both are where a soft deck usually feels cheap.

### 6.1 The tile is a `<button>`

One choice solves four problems: `:active` fires reliably on iOS Safari for
buttons (it is unreliable on `<div>` without a touch handler), keyboard activation
is free, screen readers announce a control rather than a group, and focus works
without `tabindex`. Plus `touch-action: manipulation` (kills the 300ms tap delay
and double-tap zoom) and `-webkit-tap-highlight-color: transparent` (so the OS
grey flash does not fight the designed pressed state).

**No `:hover` rules at all.** On touch, hover *sticks* — a tapped tile stays
"hovered" until you tap elsewhere, so the last tile you touched impersonates a
state. There is no pointer here; do not write the rule.

### 6.2 Three phases

**1 — Pressed. Pure CSS, zero JS, fires on touch-down.**

`transform: scale(0.97)` plus a lift to `--bg-tile-hover`. Transform only, so it
is GPU-composited and cannot drop frames or reflow. This is a channel neither
active nor attention uses (§4), so it is never ambiguous. It appears before any
JavaScript runs and long before the network — which is the entire requirement.

**2 — Pending. Optimistic, JS class, from tap until response.**

The tapped tile **takes the real cyan ring**, and the previously-active tile
**loses its ring in the same frame** (§4.2). Deliberately identical to genuine
active: it will be right ~99% of the time on a local network, and inventing a
"probably active" fourth appearance would be a fourth thing to learn for a case
that resolves in ~40ms.

Two details that decide whether this feels solid or cheap:

- **Minimum hold ~120ms.** If the response lands in 20ms and the pressed state
  ends with it, the tap reads as *nothing happened*. The pressed treatment must
  persist for a floor duration regardless of how fast the wire is.
- **No spinner.** A round-trip of 20–50ms means a spinner would flash for less
  time than it takes to perceive it — pure noise. The optimistic ring *is* the
  feedback. This mirrors the sidecar's "optimistic repaint, never block" rule
  (`muxplex-deck/AGENTS.md`).

**3 — Failed. Non-negotiably visible.**

On non-2xx or timeout:

- the optimistic ring is **revoked** and the ring returns to whatever the server
  says is active — the invariant reasserts itself;
- the tile takes a **red inset ring** (`--err`), same geometry as the cyan one;
- the STATE band replaces the age with the literal word **`FAILED`**.

Red is a third hue, which is exactly the hue-dependence this system avoids — so
**the word carries the signal and the colour merely reinforces it.** Text is the
cheapest possible redundancy and it is completely unambiguous. Under total colour
loss the tile still says `FAILED`.

Plus a single 300ms shake (`translateX` keyframes, once, `prefers-reduced-motion`
off). Additive; the design is complete without it.

**Two implementation constraints that belong in the design because they are where
this gets silently broken:**

- **Minimum visible duration ≥3s, and it must survive at least one poll cycle.**
  Polling is ~2s. A naive "re-render every tile from poll data" wipes the failed
  marker before you look up — the failure becomes invisible, which is the exact
  outcome this section exists to prevent. Failed is *client-side* state and the
  poll render must not clobber it. (This is the mirror image of the
  "stale state reported as current" class that bit this project five times;
  here it would be *fresh state erasing a true report*.)
- **Failure must not be swallowed by the optimistic path.** Reverting the ring is
  a correction, not a notification. Both are required.

### 6.3 Reduced motion

Under `prefers-reduced-motion: reduce`: no shake, no pulse, and pressed becomes a
**background change only, no transform**. One rule, no judgement calls at the call
site.

---

## 7. Type scale

Three sizes, as on the key — but the *governing constraint has inverted*, and
that is the interesting part.

On the deck, size was set by acuity (the arcminute table in
`KEY_DESIGN_SYSTEM.md §2`) and the ~7-character budget fell out of it. Here, 15px
at 400mm is ≈25 arcmin against the key's PRIMARY at 16 — **acuity is free**. So
size is set by *how many characters must fit*, and the character budget drives
the number instead of the reverse.

| Role | Size | Weight | Ink | On | Contrast |
|---|---|---|---|---|---|
| **PRIMARY** — session name | `0.9375rem` (15px) | 600 | `--text` `#F0F6FF` | `--bg-tile` `#10131C` | ~14:1 |
| **SECONDARY** — STATE line | `0.6875rem` (11px) | 400 | `--text-muted` `#8E95A3` | `--bg-tile` | ~5.8:1 |
| **TEXTURE** — preview | `0.6875rem` (11px) mono | 400 | `#7D8590` | `#0B0E14` | ~4.7:1 |

**Weight is a new channel and is used deliberately.** The key had exactly one
weight and had to carry all hierarchy on size and value. Using 600 for the name
lets the size range stay narrow (15 vs 11, a ratio of 1.36) — which directly
protects the character budget that §5 identified as binding. This is the clearest
case in the whole document of a medium capability being spent on the medium's
actual constraint rather than on decoration.

**Exactly one PRIMARY per tile.** Same load-bearing rule. Two PRIMARYs means you
have not decided what the tile is for.

### The STATE line

Relative age from `last_activity_at`: `now` (<10s), `47s`, `4m`, `2h`, `3d`.
Absent → `—` (band still reserved, per §2). Relative, not absolute: `14:32`
requires the reader to do arithmetic; `4m` *is* the answer.

**Why this earns a band the physical key could not give it.** "When did this last
produce output" is genuinely decision-relevant on a control surface — it tells you
whether the thing you are about to switch to is working or stalled. The data is
already in both payloads. The key had no room; the tile does. This is an addition
the medium justifies, not a port.

---

## 8. What carries, and what I changed

### Carries — the rules, unchanged

| From `KEY_DESIGN_SYSTEM.md` | Why it still holds |
|---|---|
| **Zone model**: NAME / BODY / STATE, bands reserved whether or not they hold ink | The anti-raggedness argument is medium-independent, and *stronger* in a grid with 8px gutters than across bezels. |
| **Exactly one PRIMARY per face** | Still the discipline that forces the decision about what a tile is for. |
| **Two orthogonal state channels** — active = ring (shape), attention = filled band + inverted ink (value) | The best idea in the document. Ports verbatim, still costs zero content pixels, still survives colour loss, still keeps attention out-ranking active. |
| **No state signalled by hue alone** | Free to keep. Extended to the two new states: pressed = geometry, failed = literal text. |
| **BODY may be a field** — preview underlays the box, bottom-anchored, name composited on top | Same trick, same reason, and it is why the physical session tile is the best face in the system. |
| **`M = B + gap`** — content never enters the ring's pixels | Ported as 8px padding against a 3px ring. |
| **Palette** `#00D9F5` / `#F1A640` / `#0D1117` | Already round-tripped from `frontend/style.css`. Deck, PWA and soft deck keep speaking one language. |
| **The lowest-value element must not be the highest-contrast one** | The key's `#A8A8A8`→`#7A7A7A` correction. The PWA still has this bug at `style.css:295` (13:1 preview); the soft deck must not inherit it. |
| **No design-token layer** | Six custom properties in one stylesheet *is* the system, per `§7` and `SOFT_DECK_DESIGN.md §11`. |

### Changed — and what in the medium justifies each

| Change | Justification |
|---|---|
| Ring 2px stroke → **3px inset box-shadow** | The ring sits across an 8px gutter from a neighbour instead of floating in bezel black — less figure-ground help. `inset` is the only CSS mechanism with the key's zero-layout-cost property. |
| NAME band `rgba(0,0,0,195)` → **opaque** | Translucent text-over-arbitrary-terminal-output is a contrast lottery I cannot verify across every possible pane. The name is the one thing that must never be hard to read. Costs the faint see-through effect; buys a guaranteed ratio. |
| ~7-char truncation → **2-line wrap + a 140px grid minimum** | `S` was hardware-fixed; column count is mine. Front-truncation is fatal with shared prefixes (§5). This is the largest single change and the clearest case of a constraint dissolving. |
| One weight → **weight as a hierarchy channel** | The medium has weights. Spending it lets the size range stay narrow, protecting the binding constraint. |
| Preview is texture *by limitation* → **texture by choice, at AA contrast** | It is now genuinely readable; the use declines the invitation (§3). Contrast rises to 4.7:1 because I cannot claim decorative exemption for real content — hierarchy is preserved by the ratio to the name instead. |
| Session tile had no STATE text → **relative age** | Room exists, `last_activity_at` is already in the payload, and "is this stalled?" changes the decision. |
| No pressed state → **scale + surface lift, floor 120ms** | Physical presses are felt; touch is not. Entirely new. |
| No failure state → **red ring + the word `FAILED`, ≥3s, poll-proof** | Local render cannot fail; a network request can. Entirely new, and the place a soft deck usually feels cheap. |
| Static → **motion as an additive channel** | CSS has it, PIL did not. Additive only, never the signal, off under reduced-motion. |

### A dependency, stated so it is not broken by accident

**This design assumes tiles do not move.** Attention is a decoration *on* a tile,
which only works if the tile stays where your thumb learned it is.
`SOFT_DECK_DESIGN.md §4.3` already recommends `sort: server` as the soft deck
default for this reason. If someone later switches the default to `attention`,
the reordering destroys the spatial constancy this tile design rests on, and the
attention band becomes redundant with the sort. The two decisions are coupled.

---

## 9. What I rejected, and why

| Rejected | Why |
|---|---|
| **`bell.unseen_count` on the tile** | It answers "how many times did it ring", which does not change your decision — you tap it either way. The band already says "this wants you". It costs characters from the name budget, the one budget §5 identified as binding. |
| **Front-truncating the name with ellipsis** | These names share prefixes. `amplifier-provider-load` and `amplifier-main` both become `amplifi…`. Discards the discriminating tail and fails *silently*. |
| **Shrink-to-fit / JS text measurement** | A measure loop over ~20 tiles on every 2s poll, to solve a problem the grid minimum solves with one declaration. Also produces sub-11px names for the longest sessions — worst legibility exactly where the name matters most. |
| **Marquee / scrolling long names** | Motion as a solution to a space problem. Unreadable at a glance, battery-hostile, and a grid of them is a slot machine. |
| **Any `:hover` treatment** | Hover *sticks* on touch. The last tile you tapped would impersonate a state indefinitely. There is no pointer here. |
| **`border` for the active ring** | Changes the box unless `box-sizing` is exactly right, and collides with the tile's own border. `outline` is worse — it draws outward into the gutter, and is reserved for focus. |
| **A separate hue for "active AND attention"** | Two states already have two orthogonal channels that compose without touching. A third hue for the combination re-creates the "muddy target" the key system spent §3 eliminating. |
| **Size-class variants / container queries** | The tile does not need to change layout at any width — the grid absorbs the variation by changing column count. Container queries would add a mechanism to solve a problem that no longer exists. One fluid component, as required. |
| **A progress spinner during the request** | Local-network round-trip is ~20–50ms. A spinner that appears for 40ms is a flash of noise. The optimistic ring is the feedback. |
| **A distinct "pending" appearance separate from active** | A fourth state to learn, for a condition that resolves in ~40ms and is correct ~99% of the time. Optimism means committing to the optimistic appearance. |
| **Icons / SVG for bell or active** | Forbidden by the brief, and unnecessary: there is nothing an icon says here that a ring, a band, or the word `FAILED` does not say more robustly. |
| **Absolute timestamps in STATE** | `14:32` requires the reader to subtract. `4m` is the answer. |
| **Long-press actions (create / delete / clear-bell)** | `SOFT_DECK_DESIGN.md §7.1` already says nothing in v1. A control surface with hidden verbs is not glanceable. |
| **Preview at PWA contrast (`#c9d1d9`, ~13:1)** | Makes the least important element the highest-contrast one — the exact defect `KEY_DESIGN_SYSTEM.md §4` corrected on the hardware side. Inheriting it would undo that work. |
| **Wrapping the preview text** | Destroys the column structure that makes the shape recognisable, which is the entire reason the preview is kept (§3). |
| **A design-token / theming layer** | Same answer as both prior documents. Six custom properties in one stylesheet is the system. |

---

## 10. What needs a real phone

None of this is settleable from here; every item has a fallback so nothing blocks.
Ordered by risk.

| # | Question | Fallback |
|---|---|---|
| 1 | **Is the preview actually shape-at-a-glance at 4.7:1, or does it pull the eye and invite reading?** The whole §3 call rests on this, and it is the one I am least able to predict — it is a question about attention, not perception. | If it pulls: drop to ~3.5:1 and accept it as decorative-adjacent, or cut the preview to 2 lines. If it is invisible: raise to 6:1. |
| 2 | **Is a 3px cyan inset ring unmistakable peripherally at 140px, across an 8px gutter?** The physical analogue of this was the highest-risk item there too, and was never settled from a mock. | 4px. `M = 8` already guarantees clearance at 4px, so nothing else changes. |
| 3 | **Does `:active` fire fast enough on iOS Safari in standalone PWA mode, and does the 120ms floor read as responsive rather than laggy?** iOS touch handling in standalone differs from in-browser. | If `:active` is unreliable, add `pointerdown`/`pointerup` classes. If 120ms reads laggy, drop to 80. |
| 4 | **Does amber `#F1A640` bloom on an OLED at high brightness and smear the black name?** Same question the hardware raised; different panel technology, so the answer may differ. | Darken the band to `#C97F1E`, keep black ink. |
| 5 | **Does the 140px minimum give a column count you actually want in portrait?** This is a judgement about your grip and session count, not a fact I can derive. | Raise the minimum to get fewer, larger tiles — the design is unchanged, it is one number. |
| 6 | **Under maximum OS text scaling, does the square tile still hold 2 name lines plus a usable preview, or does the preview vanish?** The rem-based minimum should hold, but this is arithmetic meeting reality. | Relax `aspect-ratio` to a `min-height` at large text sizes — the one place a size-class rule may be unavoidable. |
| 7 | **Is a ≥3s failed marker noticed if you tapped and looked away?** The failure mode of a control surface is a tap you believe worked. | Extend to "until the next tap or 10s". |
| 8 | **Do names break at the hyphen where expected across iOS and Android system fonts?** Browsers break after `-` by default, but line-breaking is font- and locale-dependent. | Insert `<wbr>` at hyphens when rendering — a few characters of JS, no CSS change. |

**Cheapest way to settle 1, 2 and 4:** build the page, open it on the phone with
real sessions, and look at it from where it will actually sit. Unlike the physical
deck there is no emulator gap here — the phone *is* the render target, so a
screenshot on a monitor is the only bad proxy, and it is avoidable.

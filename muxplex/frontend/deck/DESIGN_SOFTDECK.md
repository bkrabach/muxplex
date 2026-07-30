# Soft Deck — a phone as another deck in the capability space

**Design only. No implementation code.** Where this contradicts what `deck.css` /
`deck.js` do today, the code is what ships and this is the target.

**Target:** Android, landscape, fullscreen, installed. One user, personal tool.

---

## 0. The correction this document is written against

> *"I want it to **look** like the Stream Deck does… Only the # of controls that fit on
> screen (adaptive/responsive), only the kinds of controls we have in the Stream Deck
> options, no other things like scrolling or drop down menus or such, it's meant to be a
> software version of the same hardware experience."*

The current `/deck/` is a responsive web grid that happens to show sessions. It scrolls, it
has a header bar, and it opens a slide-up sheet to change views. Every one of those is a
web affordance with no hardware counterpart, and together they are why it reads as a web
page rather than a deck.

**The reframe:** a phone is not a new medium requiring a new design. It is another entry in
the capability space the sidecar already handles — `{key_count, key_rows, key_cols,
dial_count, is_touch}`. The sidecar has never known what deck it is driving and has never
needed to. Derive those five numbers from the viewport and everything downstream —
layout planning, the reserved control keys, paging, the picker, the 19-action catalog, the
key face design system — applies unchanged.

That is the whole idea. The rest of this document is the derivation, the places the medium
genuinely differs, and what I got wrong the first time.

**This supersedes:** `DESIGN_LAYOUT.md` entirely, and `DESIGN_TILE.md` / `DESIGN_RESPONSIVE.md`
in part. Exact section-by-section disposition in §12.

---

## 1. The capability derivation

### 1.1 The one number everything hangs off

The physical system expresses every geometric and typographic token as `f(S)` where `S` is
the key face edge in pixels — `NAME_H = 0.28·S`, `PRIMARY = 2S/9`, and so on
(`KEY_DESIGN_SYSTEM.md` §1, §2). **That single decision is what makes this port possible.**
Pick an `S` for the soft deck and the entire face design system arrives intact, correctly
scaled, with no new numbers invented.

So the derivation reduces to: *what is `S`, and how many of them fit?*

### 1.2 Deriving `S` from first principles, not from the web doc's tile floor

The physical key is 18 mm square viewed at ~600 mm: a visual angle of ~1.72°, about
103 arcminutes.

A phone's CSS pixel is an angular unit by definition (1/96 in at a nominal arm's length), and
real Android hardware honours it closely — a 412 CSS px viewport across ~2.7 in of glass is
~144 CSS px per inch, i.e. **5.67 CSS px per mm**. At a propped-beside-the-laptop distance of
300–500 mm (call it 400 mm), matching the hardware key's *angular* size gives:

```
400 mm · tan(1.72°) = 12.0 mm = 68 CSS px
```

68 px would be angularly correct and legibly correct. It is **touch-incorrect**. A physical
key announces its own boundary through the bezel — you can find it without looking, which is
the entire point of a deck. Glass gives you none of that. The eyes-off premium is real and it
is the one place the medium demands more, not less.

```
S_TARGET = 88 CSS px          (≈ 15.5 mm on a phone)
```

Cross-checks, all of which must pass or the number is wrong:

| Check | Result |
|---|---|
| Character budget: content width `S − 2M = 0.889·S = 78 px`; PRIMARY `2S/9 ≈ 20 px` at weight 600 (~0.56 em advance ≈ 11 px) | **~7.1 characters** — the hardware's exact budget (`KEY_DESIGN_SYSTEM.md` §2) |
| Touch: 88 CSS px = 15.5 mm | **2.0×** Material's 48 dp (7.6 mm) and WCAG 2.5.5's 44 px |
| Angular: 15.5 mm at 400 mm | 133 arcmin — **1.29× the hardware key**, which is the eyes-off premium, stated |
| Preview: content width 78 px at TEXTURE 11 px mono (~6.6 px advance) | 11–13 columns — **Stream Deck Original class** (§4.4) |

The character budget landing on 7 without tuning is the load-bearing confirmation. It means
`MAX_SESSION_LABEL_CHARS`, truncation rules, and every judgment in `KEY_DESIGN_SYSTEM.md` §5
about density transfer without adjustment.

```
S_MIN = 72    below this the 7-character budget breaks; still 1.7× the a11y floor
S_MAX = 160   above this a key is a poster, not a key
```

`S_MIN` is a **guard, not a search bound** — the search in §1.3 starts at `S_TARGET` and only
ever moves up, so `S_MIN` can only be violated by a box too small to hold a single row at
target size (a phone in a split-screen sliver). When it is violated, drop a row, then a
column, until `S ≥ S_MIN`; if even `1 × 1` cannot reach it, render the disconnected takeover
face (§7) rather than an illegible grid. Failing loudly beats a deck you cannot read.

### 1.3 Deriving the grid

Keys are square, laid on a uniform pitch, and the surround is bezel. That is what a Stream
Deck *is*, and it is also — usefully — a constraint that removes a degree of freedom: given a
content box, the aspect ratio of the grid is forced to track the aspect ratio of the box.

Two scalars, deliberately named apart because conflating them is the easy mistake:
**`S_fit`** is the search candidate — it sets the gap and decides *how many* keys fit.
**`S`** is the final token scalar every `f(S)` formula reads, derived from the resulting cell.

```
W, H   = viewport minus env(safe-area-inset-*)      the content box
g      = round(S_fit / 8)                           inter-key gap, f(S) like everything else
p      = S_fit + g                                  pitch

C = max(1, floor((W + g) / p))
R = max(1, floor((H + g) / p))
```

`floor` deliberately, not `round`: it always errs toward **bigger keys, fewer of them**.
Paging exists to absorb that error; illegibility has no such escape.

**The ceiling.** Left alone, a tablet computes 96 keys. That is not a deck. The bound is not
geometric — it is **pre-attentive scan capacity**: a grid you can take in with one glance and
navigate from spatial memory tops out around 30–40 items. The hardware family's own ceiling
corroborates it: the XL is 32.

```
N_MAX = 32
```

When the box would hold more, **grow the keys, don't add them**:

```
for S_fit in 88, 92, 96, … up to S_MAX:
    compute C, R
    if C · R ≤ N_MAX: stop
if still over at S_MAX: drop the dimension whose removal keeps
    the grid aspect (C/R) closest to the box aspect (W/H)
```

**Fit, then stretch or letterbox.** Distribute the leftover:

```
cell_w = (W − (C−1)·g) / C
cell_h = (H − (R−1)·g) / R

if max(cell_w, cell_h) / min(cell_w, cell_h) ≤ 1.15:
    use them as-is — the grid fills the box, cells imperceptibly non-square
else:
    both = min(cell_w, cell_h); centre the grid; the remainder is bezel

S = min(cell_w, cell_h, S_MAX)      the scalar every f(S) token reads
```

Note `S ≠ S_fit`. `S_fit` decided *how many* keys; `S` describes the key you actually got,
which is usually a little larger because `floor` left slack. Every `f(S)` token reads `S`;
only `g` and the grid count read `S_fit`.

A 1.15 tolerance is invisible and buys back most of the letterboxing on real devices. Past
it, letterbox — a deck floating in black on a big screen is *more* hardware-faithful than a
stretched one, not less. The surplus on the long axis, when stretching, goes to the BODY
band (vertical) or to content width (horizontal), which is where it is most useful.

### 1.4 The capability dict

```
caps = {
  key_count: R · C,
  key_rows:  R,
  key_cols:  C,
  dial_count: 0,          see §2
  is_touch:   false,      see the trap below
}
```

> **`is_touch` means "has an LCD touch *strip*", not "keys are touchable."** It is a
> secondary display (`layout.py` — FULL mode requires `dial_count ≥ 2 **and** is_touch`; the
> Deck+ headline lives there). A phone has no strip. The next person to read this will get it
> wrong, so the adapter should name its local variable `has_touch_strip`.

Consequence: `dial_count = 0` and `is_touch = false` put the soft deck squarely in
**REDUCED** mode — the mode with the most proven layout logic, shared with the Original V2
and the Mini. Nothing new is being invented.

---

## 2. Dials: none. Decisively.

**`dial_count = 0`. The soft deck is keys-only, like the Original V2 and the Mini.**

Four reasons, in descending weight:

1. **The user's constraint settles it.** "Only the kinds of controls we have in the Stream
   Deck options." Dials are one of those options — but so is *not having dials*. Two of the
   three hardware layouts in this project's own matrix are keys-only. The soft deck joins
   them; it does not invent a fourth thing.
2. **A gesture is an invisible affordance.** The physical system's hardest rule is
   "no hover, no focus, no scroll — everything a face will ever say is on it now"
   (`KEY_DESIGN_SYSTEM.md` §0). A swipe-to-cycle-views is nowhere on any face. It is
   precisely the progressive disclosure §7 of that document rejects, wearing a gesture
   costume.
3. **A dial's essential property doesn't survive the translation.** It is unbounded relative
   input, delivered without occluding the display and without acquiring a target. Every
   touchscreen analogue fails at least two of those three.
4. **It would fork the action catalog.** The three `*_cycle` actions are `relative` kind
   (`CONTROL_MAPPING_DESIGN.md` §2.0), valid on `dial.N.turn` only. With no dials they are
   simply out of scope — no new kind, no coercion, no second binding table. The two-kind
   split stays honest.

The `*_cycle` actions lose nothing the deck can't already do: `view_prev`/`view_next` and
`page_prev`/`page_next` are their momentary twins and are already in the catalog.

**What replaces the touch strip.** Nothing, and that is correct. On strip-less hardware the
sidecar spends a *key* on status (`KEY_DESIGN_SYSTEM.md` §6.5). I am not doing that either —
see §7.

---

## 3. Layout planning: the port, and its one real risk

`default_bindings(caps)` and `_reserved_control_keys(rows, cols)` (`layout.py:147–224`) run
against the derived caps with **no modification to their logic**:

- `dial_count < 2` → REDUCED.
- `cols == 3 and rows ≥ 2` → bottom control row, reading PREV / VIEW / NEXT.
- Everything else → corners: **VIEW top-left (`key.0`), PREV bottom-left, NEXT bottom-right.**
- `key_count < 4` or colliding corners → degenerate: every key a session tile, no controls.

**Three keys spent on navigation. Always. At every grid size.** Not a fraction — a constant,
because that is what the hardware does and because the alternative (scale controls with grid
size) means the control keys move when you rotate the phone, which destroys the muscle memory
the whole surface exists to build. 3/21 = 14% on a compact phone, 3/32 = 9% on a flagship.
Both fine.

**The grid is a pure function of viewport and density. Never of session count.** A deck whose
geometry reflows when a session dies is not a deck. This is why "size the grid to the working
set" is rejected (§10).

### 3.1 The risk, named

`layout.py` is Python in `muxplex-deck`; the soft deck is vanilla JS in `muxplex`. There is no
shared module and adding a cross-repo dependency for ~40 lines of arithmetic would be worse
than the duplication. So the rule gets **ported**, and two implementations of one rule drift.

Mitigation, and it should be built with the port, not after: a **shared golden fixture** —
one JSON table of `caps → expected bindings` covering the hardware fixtures
(`CAPS_ORIGINAL_15`, `CAPS_MINI`, `CAPS_XL`, degenerate grids) plus the derived soft-deck
grids from §5, checked into both repos and asserted by tests on both sides. ~30 lines each.
If the rule changes in one place, the other repo's suite fails.

### 3.2 Which of the 19 actions apply

| Applies, is a default | Applies, implementable | Out of scope |
|---|---|---|
| `session`, `view_picker`, `page_prev`, `page_next`, `none` | `view_prev`, `view_next`, `view_all`, `page_first`, `page_last`, `toggle_last`, `refresh_now` | `brightness_*` (OS owns it), `focus_app` (no OS access), `view_cycle` / `page_cycle` / `brightness_cycle` (relative kind, no dials) |

**v1 ships defaults only — no override surface.** The soft deck has no config file and adding
a server-side one is a second configuration surface for a five-entry table. But dispatch must
be written against *action names* from the catalog, not against `if (key === viewKey)`, so
that overrides are a purely additive change later. This is the same discipline
`CONTROL_MAPPING_DESIGN.md` §11.5 applies to parameterized actions: say it in the doc, don't
build it.

---

## 4. The key face: what carries, what changes

`KEY_DESIGN_SYSTEM.md` is the primary reference. Its **zone model, geometry formulas, type
ratios, state channels, and colour model all carry.** Its *pixel values* do not — but they
were never meant to; they are `f(S)` evaluated at 72.

### 4.1 Carries unchanged

- **The zone model.** NAME / BODY / STATE, uniform margin, reserved whether or not they hold
  ink, everything horizontally centred.
- **Geometry as `f(S)`.** `M = round(S/18)`, `NAME_H = round(0.28·S)`,
  `STATE_H = round(0.19·S)`, `BODY_H = cell_h − 2M − NAME_H − STATE_H`, content width
  `cell_w − 2M`.
- **Three type sizes, two ink values, one PRIMARY per face.** `PRIMARY = round(2S/9)` at
  `#FFFFFF`, `SECONDARY = round(11S/72)` at `#8888AA`, `TEXTURE = 11 px fixed` at `#7A7A7A`.
- **TEXTURE does not scale with `S`.** Its value is column count, not apparent size. Same
  reasoning, same conclusion.
- **Two orthogonal state channels.** Active = ring at the face edge. Attention = NAME band
  fills amber `#F1A640`, band ink inverts to `#000000` (10.4:1). They never collide because
  the band is inset by `M`. Attention outranks active.
- **Colour model.** `#0A0A0A` session · `#101036` control/chooser · `#000000` empty; cyan
  `#00D9F5`, amber `#F1A640`. No state is signalled by hue alone.
- **BODY as a field.** The terminal preview underlays the whole content box, bottom-anchored,
  with the NAME band composited over it.
- **The §6.2 control-key content table, verbatim** — including the critical inversion
  (`< PREV` in SECONDARY over `PAGE` in PRIMARY, not the reverse), which exists to stop
  `view_prev` and `page_prev` being confusable. That failure mode is *worse* here, because
  soft keys sit closer together than bezel-separated hardware keys.
- **ASCII chevrons.** No icon set, no arrow glyphs. The reason was a PIL font limitation that
  no longer applies — but the *conclusion* stands on its own: six ASCII words work, and an
  icon set is a dependency bought to replace them.

### 4.2 Changes, each with the property of the medium that forces it

| Change | Why |
|---|---|
| **Weight becomes available.** PRIMARY 600 / SECONDARY 500 / TEXTURE 400. | The physical system's single most binding constraint — "`load_default()` is Aileron Regular, there is no bold" (§0) — is gone. §7 rejected weight hierarchy on *packaging cost*, not on principle. The cost is zero here, so take it. Note it slightly *tightens* the character budget (600 is wider than 400); the §5 worked examples account for it. |
| **`B = max(2, round(S/30))`** instead of `max(2, round(S/36))` → 3 px at S≈90, 5 px at S=160. | Hardware keys are isolated by a physical black bezel, so a ring is a lone bright edge. On glass, 32 faces are adjacent across an 11 px gap. The ring needs more weight to separate. `M = S/18` still clears it (`M ≥ B+1`), so §3's collision resolution holds. This also pre-empts the hardware system's open question #1 by taking its own fallback up front. |
| **Every face gets a 1 px `#1C1C22` outline.** | Same cause. `#0A0A0A` faces on a `#000000` surround merge without the bezel to separate them. The outline restores the edge the hardware got for free from the plastic. |
| **Press feedback: `scale(0.96)` + `brightness(1.15)`, 100 ms, applied optimistically and rolled back on failure.** | The one thing glass genuinely owes you. A physical key has travel; this is its replacement, not decoration. Mandatory, and it is the *only* motion in the system — motion stays an additive channel, never the signal. `prefers-reduced-motion` swaps scale for a 1-frame brightness flash. |
| **The preview is at the bottom edge of *readable*, not below it.** | At 11 px with subpixel AA at DPR ≥ 2, cap height ≈ 8 CSS px ≈ 12 arcmin at 400 mm — above the ~10 arcmin letter-identification threshold that put the hardware preview honestly below it (§2). The accurate label is **recognisable phrases**: `906 passed`, `$ pytest -q`, `Waiting for…`. Not prose. |
| Ink stays `#7A7A7A` anyway. | §4's correction — the least important thing on the face must not be the highest-contrast thing on it — is unchanged by the preview becoming marginally more legible. Naming it precisely matters so nobody later "improves its legibility" by shrinking it and adding columns, which would undo both. |

### 4.3 What does *not* change that you might expect to

The vertical-centring rule (centre a fixed reference string's ink bbox, not each string's own)
was a real defect fix in PIL. On the web it is free — fixed band heights plus `line-height`
give it automatically. The rule still holds; it just costs nothing to honour.

### 4.4 Preview parity, stated honestly

| Deck | Content width | Columns × lines |
|---|---|---|
| Original (S=72) | 64 px | ~11 × 4 |
| Soft, flagship phone (S≈91) | 89 px | 13 × 6 |
| Deck+ (S=120) | 106 px | 21 × 8 |
| Soft, tablet (S=160) | 147 px | 22 × 13 |

A soft deck on a phone is **Original-class** for preview, not Deck+-class. On a tablet it
beats the Deck+. Stated so nobody is surprised.

---

## 5. Worked examples

`g = round(S/8)`, `S_TARGET = 88`, `N_MAX = 32`, aspect tolerance 1.15.

### 5.1 Compact Android phone, landscape — 780 × 360 CSS

Content box after insets (cutout side 30, gesture pill 16): **W = 750, H = 344**

```
S_fit = 88, g = 11, p = 99
C = floor(761/99) = 7        R = floor(355/99) = 3        N = 21 ≤ 32  ✓
cell_w = (750 − 6·11)/7 = 97.7      cell_h = (344 − 2·11)/3 = 107.3
ratio 1.098 ≤ 1.15 → stretch, fills the box
S = min(97.7, 107.3, 160) = 97.7 → 98
```

| Token | Value | | Token | Value |
|---|---|---|---|---|
| grid | **3 × 7 = 21 keys** | | PRIMARY | 22 px / 600 |
| `B` | 3 | | SECONDARY | 15 px / 500 |
| `M` | 5 | | TEXTURE | 11 px / 400 |
| `NAME_H` | 27 | | name budget | ~7.1 chars |
| `STATE_H` | 19 | | preview | 13 cols × 7 lines |
| `BODY_H` | 51 | | | |

Bindings (corners, since `cols ≠ 3`): `key.0` = VIEW · `key.14` = PREV · `key.20` = NEXT.
**18 session slots per page.**

### 5.2 Flagship Android phone, landscape — 915 × 412 CSS

Content box after insets (cutout 45, gesture pill 16): **W = 870, H = 396**

```
S_fit = 88, g = 11, p = 99
C = floor(881/99) = 8        R = floor(407/99) = 4        N = 32 ≤ 32  ✓  (exactly at the ceiling)
cell_w = (870 − 7·11)/8 = 99.1       cell_h = (396 − 3·11)/4 = 90.75
ratio 1.092 ≤ 1.15 → stretch, fills the box
S = min(99.1, 90.75, 160) = 90.75 → 91
```

| Token | Value | | Token | Value |
|---|---|---|---|---|
| grid | **4 × 8 = 32 keys** | | PRIMARY | 20 px / 600 |
| `B` | 3 | | SECONDARY | 14 px / 500 |
| `M` | 5 | | TEXTURE | 11 px / 400 |
| `NAME_H` | 25 | | name budget | ~8.0 chars |
| `STATE_H` | 17 | | preview | 13 cols × 6 lines |
| `BODY_H` | 39 | | | |

Bindings: `key.0` = VIEW · `key.24` = PREV · `key.31` = NEXT. **29 session slots per page.**

**A flagship Android phone in landscape derives, without snapping, to a 4 × 8 grid — the
Stream Deck XL's exact geometry.** That is not a coincidence worth dismissing: a 6.2″ phone
in landscape is ~150 mm wide; an XL's key area is ~160 mm. The physical sizes genuinely
match. The derivation landing on a real deck shape is the strongest available evidence that
`S_TARGET = 88` is the right number.

Against 43 sessions in the `all` view: 2 pages (29 + 14), `p1/2` in the PREV/NEXT STATE bands.

### 5.3 Android tablet, landscape — 1280 × 800 CSS

Content box: **W = 1264, H = 784**

```
S_fit = 88  → C = 12, R = 8 → 96 keys.  Over N_MAX. Step up.
S_fit = 104 → 60.   S_fit = 120 → 45.   S_fit = 132 → 40.   S_fit = 140 → 40.
S_fit = 144, g = 18, p = 162 → C = floor(1282/162) = 7, R = floor(802/162) = 4 → 28 ≤ 32  ✓
cell_w = (1264 − 6·18)/7 = 165.1     cell_h = (784 − 3·18)/4 = 182.5
ratio 1.105 ≤ 1.15 → stretch, fills the box
S = min(165.1, 182.5, S_MAX) = 160          ← clamped by S_MAX
```

| Token | Value | | Token | Value |
|---|---|---|---|---|
| grid | **4 × 7 = 28 keys** | | PRIMARY | 36 px / 600 |
| `B` | 5 | | SECONDARY | 24 px / 500 |
| `M` | 9 | | TEXTURE | 11 px / 400 |
| `NAME_H` | 45 | | name budget | ~7.3 chars |
| `STATE_H` | 30 | | preview | 22 cols × 13 lines |
| `BODY_H` | 89 | | | |

Bindings: `key.0` = VIEW · `key.21` = PREV · `key.27` = NEXT. **25 session slots per page.**

Note the character budget holds at ~7 across all three viewports while `S` nearly doubles.
That is `f(S)` doing exactly what `KEY_DESIGN_SYSTEM.md` §2 promised: *"the character budget
is face-size-independent."*

### 5.4 Portrait, unforced — 390 × 844 CSS

The manifest forces landscape (§8), but before install, or if the platform declines, portrait
must not be a special case. It isn't:

```
W = 374, H = 812.  S_fit = 88, g = 11, p = 99
C = floor(385/99) = 3        R = floor(823/99) = 8        N = 24 ≤ 32  ✓
cell_w = 117.3     cell_h = 91.9     ratio 1.276 > 1.15 → letterbox at 91.9
grid 297.7 × 812 → 38 px side bezel
```

`cols == 3 and rows ≥ 2` fires `_reserved_control_keys`'s three-column special case: a
**bottom control row**, PREV / VIEW / NEXT left to right. 21 session slots.

**A portrait phone spontaneously becomes a tall Stream Deck Mini, control row and all,
because the derived caps hit a branch written years earlier for a different device.** If the
capability port were wrong, this would not happen. It is the cheapest available proof that it
isn't.

---

## 6. Paging and the picker — both taken from the hardware, verbatim

### 6.1 Paging

Confirmed as the model. `page_prev` / `page_next` on the two reserved keys; the current page
shown as `n/N` in their STATE bands (`KEY_DESIGN_SYSTEM.md` §6.2). Sessions fill
`session_slots` in order; short pages leave `#000000` blank faces rather than reflowing.

This **overturns `SOFT_DECK_DESIGN.md` §4.2**, which declared the six `page_*` actions
"meaningless here" on the grounds that scroll replaces paging. Scroll is gone, so they are
meaningful again — and views were never a substitute for paging: a view is a semantic filter
the user curates, a page is arithmetic overflow that happens whether they curated or not.

### 6.2 The picker

The hardware model, unchanged: **the picker takes over the whole surface.**

- Tapping VIEW replaces every face's *content*. **The grid geometry does not change** —
  same `R × C`, same `S`, same positions. Mode changes what faces say, never where they are.
- The three reserved keys become **BACK / PREV / NEXT** (`KEY_DESIGN_SYSTEM.md` §6.4), at
  their same positions. BACK reads NAME `< BACK` / BODY `VIEW`.
- Every other key is a view option. Options page with the same PREV/NEXT.
- The current view carries the cyan ring — same channel as the active session, because it is
  the same meaning: *this is the live one*.
- Tap → `PATCH /api/state` → return to the grid.

**The slide-up sheet is deleted.** It is the clearest instance of the thing the user rejected.

**One deliberate enrichment.** `KEY_DESIGN_SYSTEM.md` §6.3 leaves a picker option's NAME and
STATE bands reserved-but-empty — NAME because all twelve options would say the same word, and
STATE because a 72 px face has nothing to put there. A soft key at 91–160 px does:

| Band | Picker option content |
|---|---|
| NAME | — (reserved, empty; unchanged) |
| BODY | view name — **PRIMARY** |
| STATE | `n sessions`, with the attention marker if any session in that view wants you |

This is not new chrome. It is ink in a band the zone model already reserved, and it converts
view selection from recall ("which one had the build in it?") to recognition — the one
genuine capability the extra surface buys. It is the only thing I have added to the face
system.

---

## 7. Status, staleness, and failure — without spending a key

Strip-less hardware puts errors on a key (`KEY_DESIGN_SYSTEM.md` §6.5). I am not doing that,
because on a 21-key deck a permanently reserved status key costs 5% of the surface to display
"fine" 99% of the time.

Instead, two whole-surface signals — neither of which is chrome, because neither occupies
space:

| Condition | Signal |
|---|---|
| **Stale** — poll late but not failed | Every face drops to 55% opacity. A global *value* change, which is the same channel §4 already uses for attention. Zero keys spent. Unmistakable, and it degrades to "everything looks dimmer" rather than to a lie. |
| **Disconnected** — poll failed repeatedly | Surface takeover: the grid is replaced by one message face plus a RETRY key. Identical mechanism to the picker. |

The two questions a header would have answered — *which view am I in* and *what page* — are
already answered by the VIEW key's BODY and the PREV/NEXT keys' STATE. **The header is not
replaced. It is unnecessary.**

---

## 8. Fullscreen, landscape, installed — on Android

The requirement: launches like a game, not like a browser tab; forces its own orientation.

| Mechanism | Detail |
|---|---|
| `manifest.json` | `"display": "fullscreen"` (not `standalone` — fullscreen also removes the status bar), `"orientation": "landscape"`, icons at 192 and 512, `start_url: "/deck/"`, `background_color`/`theme_color` `#000000`. |
| Orientation | Manifest `orientation` is honoured by Chrome on Android **only in an installed standalone/fullscreen context.** In a browser tab it is ignored — which is exactly what the user observed when "Add to Home Screen" produced a browser shortcut. |
| Belt and braces | `document.documentElement.requestFullscreen()` on first user gesture, then `screen.orientation.lock('landscape')`. Redundant when the manifest works; the fallback when it doesn't. |
| Safe areas | `viewport-fit=cover` plus `env(safe-area-inset-*)` on the deck surround. Mandatory — every number in §5 is computed against the inset box, and without `viewport-fit=cover` every inset resolves to `0px`. |
| Wake lock | `navigator.wakeLock.request('screen')`, re-acquired on `visibilitychange`. Baseline since 2024. |
| Scroll prohibition | `position: fixed` on the surround, `overscroll-behavior: none`, `touch-action: manipulation` on faces, no scrollable ancestor anywhere. |

### 8.1 The one addition I am arguing for explicitly

**A minimal service worker.** Chrome on Android gates real installability — the path that
produces a fullscreen, orientation-locked launcher rather than a browser shortcut — on a
service worker with a `fetch` handler, alongside the manifest and a secure origin.

It is not an interaction affordance, has no UI, and the user can never encounter it. It is
the install gate. ~20 lines, no build step, cache-nothing (`fetch` passthrough) — this
surface is useless offline by design, so caching would only create staleness risk in a
project that has already paid for stale-state bugs five times.

Everything else on the "web affordance" list is rejected: no long-press, no swipe, no
pull-to-refresh, no sheets, no dropdowns, no scroll.

> **Amended 2026-07-30 -- this rejection did not survive contact with a real user.**
> BACKLOG.md item 2 (a settings menu inside the soft deck) shipped in v0.27.0 with
> long-press as its *only* entry point, directly contradicting the line above. The
> user who commissioned the feature, on the day it shipped, could not find it and
> concluded it was broken -- a product council later returned 2 FAIL / 4 CONCERN,
> one FAIL naming the accessibility consequence directly: a bare pointer-timer has
> no node in the accessibility tree, so TalkBack (or any assistive tech) cannot
> surface it at all, and what sits behind it is brightness/grid-size controls --
> exactly the accommodations a low-vision user needs. The rejection above was
> right about *sheets, dropdowns, pull-to-refresh, and scroll* -- those remain
> rejected. It was wrong to fold long-press into the same bucket without asking
> whether *something* needed to be added to reach a form-entry surface a phone
> uniquely enables (\u00a76.2's "form data entry" already carves out the one deliberate
> scroll exception for the settings panel itself; the same phone-specific reasoning
> applies to how you get there).
>
> **Current shape (v0.28.x+):** a real key -- `role: 'settings'`, a normal
> `<button>` with an explicit `aria-label` -- on the view picker page (tap VIEW,
> SETTINGS is one of the keys that appears, present on every page, absent only
> when the grid is too small to have controls at all). This costs zero permanent
> pixels and zero permanent key slots on the main grid, which is what actually
> satisfies "must not consume a key slot" -- the constraint this document's
> long-press rejection was trying to serve in the first place. Long-press on VIEW
> remains as an accelerator for a sighted, steady-handed user who already knows it
> exists, with an 8px movement tolerance (matching `DIAL_TAP_PX_THRESHOLD`) and a
> visible fill-ring while held, so a hold that fails is no longer silent. It is
> never the *only* way in again.

### 8.2 The gate this all sits behind

Real install requires a **secure origin**. `spark-1:8088` presents a leaf signed by a
private CA. Android's user-CA store is honoured by Chrome for browsing, but whether Chrome
treats such an origin as installable — and whether it degrades the install to a shortcut,
which is precisely the failure already observed — **is unverified and cannot be verified from
here.** It is item #1 in §11.

The known fallback is `muxplex setup-tls --method tailscale`, which was deliberately deferred
because it breaks both hardware sidecars in lockstep and adds an unautomated 90-day renewal.
That deferral stands. But if the CA blocks install, the soft deck's core requirement is
blocked, and the trade changes.

---

## 9. What the physical system's open questions become here

`KEY_DESIGN_SYSTEM.md` §8 lists eight things unresolvable without hardware. They map:

| Hardware Q | On an OLED phone |
|---|---|
| 1 · `B=2` ring visible peripherally? | **Pre-empted.** Taken to `B = max(2, round(S/30))` = 3–5 px up front (§4.2), since glass lacks the bezel isolation that made 2 px plausible. Still needs eyes. |
| 2 · Does amber `#F1A640` bloom? | Different physics — OLED bloom is generally *less* than backlit LCD. Same fallbacks apply (`#C97F1E` band, black ink). |
| 3 · SECONDARY at 8 px cap recognisable? | Larger here: 14–24 px SECONDARY vs 11. Likely resolved by the medium. |
| 4 · Does the NAME band read as continuous across bezel gaps? | **Better here** — an 11 px gap versus ~4 mm of plastic. The cross-key band claim is more likely to hold, not less. |
| 5 · Session NAME top vs control BODY middle — ragged? | Unchanged. Same accept. |
| 6 · Empty STATE band looks bottom-light | Unchanged, and slightly worse at 28–32 adjacent faces. Same accept. |
| 7 · Preview at `#7A7A7A` still recognisable? | Better — subpixel AA at DPR ≥ 2. |
| 8 · Picker at PRIMARY 16 vs 20 | Moot; PRIMARY is 20–36 here and picker options now carry a STATE line (§6.2). |

---

## 10. What I rejected, and why

| Rejected | Why |
|---|---|
| **Snapping the grid to a hardware shape (3×5 / 4×2 / 3×2)** | Model-driven layout wearing a disguise. `AGENTS.md`'s central rule forbids it, and the derivation lands on hardware shapes anyway (§5.2, §5.4) — which is only meaningful *because* it wasn't forced. |
| **Any dial analogue via gesture** | Invisible affordance, hidden state, forks the action catalog's two-kind split. §2. |
| **A touch-strip analogue (a status/header bar)** | The strip is a *display*, and its screen equivalent is a header — exactly the chrome the user rejected. §7 shows it is unnecessary, not merely unwanted. |
| **The bottom sheet for view selection** | The single clearest violation of the brief. Replaced by the hardware's surface takeover. §6.2. |
| **Scroll, in any form** | `DESIGN_LAYOUT.md` §4 argued scroll "defines the contract's boundary" rather than breaking it. That reasoning was sound for a web page and wrong for a deck: a deck's contract is that *everything you can reach is visible*, and paging is how the hardware honours it under overflow. |
| **Sizing the grid to the session count** | Geometry would reflow as sessions come and go. Muscle memory is the entire value of a fixed key grid; nothing is worth trading it for. |
| **Growing key count without bound on large screens** | 96 keys on a tablet is a search problem, not a deck. `N_MAX = 32`, grow the keys instead. §1.3. |
| **Stretching cells to fill regardless of aspect** | Past ~1.15 the cells stop reading as keys. Letterbox instead — bezel is what a deck on a big surface looks like. |
| **Computing the layout server-side** | Only the client knows the viewport, the insets, and the DPR. A round trip to learn them is latency bought for nothing. |
| **Importing `layout.py` across repos** | A packaging dependency for ~40 lines of arithmetic. Port the rule, defend it with a shared golden fixture. §3.1. |
| **A native Android app / TWA** | Already settled in `SOFT_DECK_DESIGN.md` §3. Fullscreen + forced orientation + wake lock are all available to an installed PWA; the residual native win is haptics on iOS, which is not the target. |
| **Per-device breakpoints or a size-class table** | `f(viewport, density)` covers every case including ones that don't exist yet, and §5.4 shows portrait needs no branch. |
| **An override/config surface in v1** | A second configuration surface for a five-entry default table. Dispatch by action name so it stays additive. §3.2. |
| **`brightness_*` and `focus_app`** | The browser owns neither screen brightness nor OS foreground. |
| **Caching in the service worker** | This surface is useless offline by design. A cache would only manufacture stale-state risk in a project that has shipped five such bugs. |
| **An icon set, gradients, rounded corners, drop shadows** | Inherited rejection from `KEY_DESIGN_SYSTEM.md` §7. The original reason (PIL cost) is gone; the conclusion isn't. Six ASCII words already work. |

---

## 11. What needs a real Android phone

Ordered by how much is blocked if it fails.

| # | Question | Fallback |
|---|---|---|
| 1 | **Does Chrome install this as a real fullscreen PWA over the private-CA origin, or downgrade it to a browser shortcut?** Blocks the user's primary complaint. | `setup-tls --method tailscale`, with the 90-day renewal check built into `doctor` in the same change. |
| 2 | **Does `"orientation": "landscape"` actually override the OS rotation lock** once installed? | `screen.orientation.lock()` after `requestFullscreen()`. If both fail, the design still works — §5.4 shows portrait derives cleanly. |
| 3 | **What are the real `env(safe-area-inset-*)` values in `display: fullscreen` landscape** with a hole-punch and gesture nav? Every number in §5 is computed against my *estimates* of these. | None needed — the algorithm consumes whatever the platform reports. But the §5 grid sizes will shift and should be re-measured, not trusted. |
| 4 | **Is a 3 px cyan ring unmistakable among 32 adjacent faces** at 400 mm, peripherally? | `B = max(2, round(S/24))` → 4 px at S≈91. `M` still clears it. |
| 5 | **Does `#F1A640` bloom on this OLED** and smear the black NAME-band ink? | `#C97F1E` band, black ink retained. |
| 6 | **TEXTURE at 11 px, DPR ≈ 2.6, 400 mm — recognisable phrases or noise?** Determines whether §4.2's "bottom edge of readable" claim is true. | If noise, it is simply what the hardware system already says it is: texture. Nothing breaks; the honest label changes back. |
| 7 | **Is a 4 × 8 grid thumb-reachable across a 6″ phone held two-handed?** This is reach, not size — a distinct failure mode from anything the hardware faced, since a Stream Deck is set down and a phone is held. | Reduce `N_MAX` toward 24, or bias the derivation toward fewer rows. Would be a genuine finding: the first constraint the physical system has no analogue for. |
| 8 | **Does the grid survive the notification shade, an incoming call, and app-switch return without reflowing?** `visualViewport` changes must not silently re-derive the grid mid-session. | Re-derive only on a settled `resize`, debounced, and never while a picker is open. |

Items 1–3 are cheap and should be settled first, in one sitting, before any face work: they
can invalidate the premise. Items 4–6 inherit hardware fallbacks and block nothing. Item 7 is
the only genuinely new risk this medium introduces.

---

## 12. Disposition of the existing design documents

| Document | Status |
|---|---|
| **`KEY_DESIGN_SYSTEM.md`** | **Primary reference. Not superseded.** §1 zones, §2 type ratios, §3 state channels, §4 colour, §6 per-key specs all apply, with the §4.2 deltas above. |
| **`CONTROL_MAPPING_DESIGN.md`** | **Not superseded.** The address grammar, the two-kind split, and the default binding table apply verbatim. Only the applicable *subset* narrows (§3.2). |
| **`DESIGN_LAYOUT.md`** | **Superseded in full.** §1 header + attention strip, §3 the 164 px tile floor and `auto-fill` grid, §4 scroll, §5 the bottom sheet, §8 orientation reflow — every load-bearing decision is reversed. Its §3.1 unit analysis (reason in CSS px, they're angular) survives and is reused in §1.2 here. |
| **`DESIGN_TILE.md`** | **Superseded in part.** Dead: §2 anatomy/geometry, §3 "preview graduates to content", §5 "widen the tile don't shrink the name", §7 the 17/12/12 type scale. Carried: §4 state without hue, §4.2 the ring as inset box-shadow, §4.3 motion as additive channel, §6 pressed/failed three phases, §6.3 reduced motion. |
| **`DESIGN_RESPONSIVE.md`** | **Superseded in part.** Dead: §1 size classes and computed columns, §5 "do not lock orientation — reflow." Carried and load-bearing: §2 touch-target floors (input to §1.2), §3 wake-lock lifecycle, §4 standalone mode / safe areas / manifest, §6 tap feedback. |
| **`SOFT_DECK_DESIGN.md`** | **One reversal.** §4.2 declared `f(S)` geometry, the ~7-character budget, TEXTURE-as-a-category, the `key.N` binding model, fixed key count, and all six `page_*` actions dead on a phone. **All six of those are restored** — they were judged against a scrolling web grid, and the surface changed. §3's native-vs-PWA verdict and §8's auth/TLS walkthrough stand unchanged. |

---

## 13. The one-line summary

**A phone is a deck with `dial_count = 0`, `is_touch = false`, and an `R × C` derived from
its own viewport — and once you say that, there is almost nothing left to design.**

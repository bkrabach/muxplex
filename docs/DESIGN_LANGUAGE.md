# muxplex — Design Language

The place to look up spacing, type, colour, elevation, layering and component
choice before adding anything to the interface. If the answer is not derivable
from this document plus `muxplex/frontend/tokens.css`, that is a defect in this
document — say so rather than inventing a sixth answer.

**Companion file:** `muxplex/frontend/tokens.css` holds every value. This file
holds the reasoning, the component vocabulary, and the rules for when each
applies. Neither is useful without the other.

**There is exactly one token file, and that is it.** If you find another set of
token values anywhere in this repo — including
`assets/branding/DESIGN-SYSTEM.md` — it is superseded and its numbers are wrong.
§3.4 records why, and `muxplex/tests/test_design_tokens.py` fails the build if a
second `tokens.css` ever reappears.

---

## 0. Why this exists

The agent chat panel shipped looking like a different product. An independent
design review found the specific reason, and it is worth quoting because it
names the failure mode this document is built to prevent:

> Five unrelated spacing/sizing families with no common unit — `10/12` padding,
> `6/8/4` bubble metrics, a `22px` logo, `min(420px, 92vw)` width, `z-index:
> 9000`. **They read as five separate eyeballings.**

A second reviewer added the shape of it: the panel imports chat-app conventions
— filled message bubbles, an elevated floating surface, a branded product header
— that appear nowhere else in a tool that describes itself as dense monitoring.

Independently confirmed in the browser (VLM read of actual pixels, muxplex
running in the `muxplex-lan-twin` container, 2026-08-15), unprompted as to which
side was "right":

> *"No, the chat panel and the rest of the app do not look like they come from
> the same product."* — differing in **background colour** (panel noticeably
> lighter), **elevation** (panel shadowed, everything else flat), **corner
> radius** (panel moderately rounded throughout, app sharp), **density** (panel
> airy, app very dense), **filled colour surfaces** (panel fills message blocks,
> app uses coloured text on the page), and **type size** (panel larger).

None of those five families was a bad choice on its own. Each was a reasonable
local decision made without a place to look up the global one. That missing
place is what follows.

**The purpose of this document is not taste. It is to make the next value
derivable instead of invented.**

---

## 1. What muxplex is

Every decision below falls out of one sentence, so it is worth stating plainly:

> **muxplex is a dense monitoring tool. You look at it to find out whether
> something needs you.**

Consequences that decide most arguments before they start:

- **Density is the product, not a compromise.** Fitting more live sessions on
  screen is the feature. Whitespace that buys "breathing room" at the cost of a
  visible session is a net loss.
- **The interface is chrome around a terminal.** The content is someone else's
  monospace output. Our job is to frame it and get out of the way.
- **Reading beats interacting.** Most time in muxplex is spent scanning, not
  clicking. Optimise for glanceability; controls recede until wanted.
- **It is a control surface, not a document.** Adopted verbatim from
  `deck/DESIGN_RESPONSIVE.md`, which rejected porting the PWA's width
  breakpoints on exactly this ground.

When a proposal would be right for a chat app, a dashboard product, or a
marketing page, and wrong for a dense monitoring tool — it is wrong here.

---

## 2. Principles

Seven. Each is testable against a specific artefact, not a slogan.

### P1. Density is the default; airiness must be argued for

Reach for the smallest step on the scale that still reads. `--space-sm` (6px) is
the app's real unit between related things; `--space-md` (8px) between
components. `--space-2xl` (24px) separates unrelated regions and is rare.

*Test:* if a new element uses more vertical space than the tiles beside it, and
it is not more important than a session, it is wrong.

### P2. Flat by default. Elevation is earned by overlap, never by grouping

All 4,077 lines of `style.css` contain **nine** `box-shadow` declarations, and
every one is on something that overlaps other content. Nothing that sits *in*
the layout is shadowed — tiles, sidebar rows and settings fields are flat,
separated by a 1px `--border` and nothing else.

*Test:* does this element cover other content the user was looking at? If no, it
gets no shadow. Grouping is done with a line, or with space, or with nothing.

This is the single most visible thing the agent panel got wrong.

### P3. Colour carries meaning; it does not decorate

One accent (`--accent`) means "interactive". Three signals (`--ok`, `--warn`,
`--err`) and one attention colour (`--bell`) mean status. A filled coloured
surface is a claim that the thing inside it has a state. Content blocks are text
on the page.

*Test:* can you say in one clause what this fill *means*? If not, remove it.

*Corollary (accessibility):* `--text-dim` is 2.4:1 and decorative by
construction. Colour is never the sole carrier of meaning — the tile bell state
pairs its colour with a border and a glow for exactly this reason.

### P4. One component, one home — and the home says so explicitly

This is the project's own hard-won rule, from the `.quick-link` consolidation
(`d9061ba`, `290b71e`). It took five rounds because CSS cascades **per
property**: a higher-specificity rule that never mentions `background` does not
beat a lower-specificity rule that sets it. The fix was structural, and its
shape generalises:

1. **One rule owns every visual property of a component, in every state** —
   rest, hover, focus-visible, active, expanded.
2. **Guarantee absences explicitly.** Write `background: transparent; border:
   none`, do not rely on no other rule setting them.
3. **Guard the structure, not the value.** The test that let four rounds of this
   bug survive checked what the shared rule declared. The test that finally
   closed it asserts that competing rules *cannot exist*.
4. **When CSS cannot reach it, change the element.** Four rounds of styling
   could not make a native `<select>` match a `<button>`, because the browser
   renders a `<select>`'s popup. Round five changed the element and unified the
   mechanism.

*Test:* to change how a component looks in any state, is there exactly one place
to edit? If not, it is not a component yet.

### P5. Every value comes from the scale, or carries its reason beside it

A number not in `tokens.css` is allowed — the app has genuine one-offs — but it
must be adjacent to a comment saying why. `--control-pad-x: 10px` is the
worked example: off-scale on purpose, named as a control token so it cannot leak
back out into general spacing.

*Test:* `git blame` the number. Does the line above it explain itself?

### P6. 48 × 48 CSS px is the adopted touch floor, and it is not negotiable

From `deck/DESIGN_RESPONSIVE.md` §2.1: the one floor satisfying Apple HIG
(44pt), Material (48dp), WCAG 2.2 AA (24px) and WCAG 2.1 AAA (44px)
simultaneously. Already cited by name inside `style.css`'s agent-panel mobile
media query. It is a **floor**, not a target — session tiles exceed it several
times over.

*Test:* on a touch viewport, is every interactive element's hit area at least
`--touch-min` square? Visual size may be smaller; the hit area may not.

### P7. A pixel change is proved in a browser

The repo's standing rule, earned three times over. Curl, DOM text and passing
tests are not evidence for anything a person sees — a broken layout still yields
perfectly valid DOM text. Screenshot the actual pixels.

*Test:* the claim "this now looks right" is backed by an image, or it is not
made.

---

## 3. Tokens

Values live in `muxplex/frontend/tokens.css`. That file is the source of truth;
this section is the decision table for choosing between them.

### 3.1 How to pick

| You need… | Use | Notes |
|---|---|---|
| Gap between related items | `--space-sm` (6px) | the app's real unit |
| Gap between components | `--space-md` (8px) | also the grid gap |
| Padding inside a panel | `--space-lg` (12px) | |
| Page margin / region separation | `--space-xl` (16px) | |
| Optical nudge | `--space-2xs` (2px) | alignment only, not layout |
| A corner radius | `--radius-sm` (4px) | default for **everything** |
| Radius on a menu or dialog | `--radius-md` (8px) | |
| Radius on a mobile sheet | `--radius-lg` (12px) | |
| Default text size | `--text-md` (13px) | the app's real base |
| Dense metadata / badges | `--text-xs` (11px) | |
| A shadow | first re-read **P2** | then `--shadow-popover` / `--shadow-modal` |
| A stacking order | §3.3 ladder | never a fresh integer |
| Interactive colour | `--accent` | one accent, one meaning |
| A status colour | `--ok` / `--warn` / `--err` / `--bell` | |

### 3.2 Where the numbers came from

The scale is a **description of the app**, not a proposal for it. Counting every
declaration in `style.css`:

- **Spacing** — `8px` (~75 uses), `6px` (~59), `4px` (~45), `12px` (~24),
  `16px` (~22), `2px` (~11) already carry essentially all spacing in the app.
- **Radius** — `4px` is 52 of 83 declarations (63%). `8px` and `12px` are the
  only other values with real usage.
- **Type** — `13/12/11px` are 83 of 125 declarations (66%). The app's base size
  is 13px.

Adopting the scale is mostly renaming numbers that were already right. That is
the point: it should feel like almost nothing changed, because for most of the
app almost nothing does.

### 3.3 The layer ladder

Measured, not designed. Every value is one `style.css` already uses:

| Token | px | Used today by |
|---|---|---|
| `--z-raised` | 1 | `.tile-device-tag` |
| `--z-view-overlay` | 10 | `.reconnect-overlay` |
| `--z-float` | 50 | `.session-pill`, expanding tile |
| `--z-popover` | 100 | `.toast`, `.view-dropdown__menu` |
| `--z-panel` | 200 | `.bottom-sheet`, mobile sidebar |
| `--z-modal-backdrop` | 299 | `.settings-backdrop` |
| `--z-modal` | 300 | `.settings-dialog`, `.flyout-menu` |
| `--z-modal-nested` | 310 | flyout submenu |
| `--z-hover-preview` | 500 | `.preview-popover` |

Two anomalies are recorded rather than quietly fixed, because reordering live
stacking is a repaint and needs P7 evidence — see §7.

### 3.4 There is exactly one token file — and why the second one was deleted

**The rule, in one sentence: every token value muxplex uses lives in
`muxplex/frontend/tokens.css`, and adding a second token file anywhere in the
repo is a defect.** `muxplex/tests/test_design_tokens.py` enforces this by
walking the tree and failing if a second `tokens.css` appears.

This rule was bought, not assumed. Until 2026-08-15 the repo shipped **two**
token files at different paths:

| | `assets/branding/tokens.css` | `muxplex/frontend/tokens.css` |
|---|---|---|
| Introduced | `8234e2e`, 2026-03-27 | `89104aa`, 2026-08-15 |
| Size | 12,716 B · 100 names | 19,234 B · 67 names |
| Linked by `index.html` | never | yes (line 31) |
| Referenced by `style.css` | never, not once | yes |
| Served over HTTP | no route, no mount | `/tokens.css` |
| In the published wheel | no (`packages = ["muxplex"]`) | yes |

Git never flagged it, because the paths differ — it surfaced only by diffing
across lanes.

**Why "two clearly-scoped layers" was not available.** A brand-primitives layer
under a UI layer is a legitimate pattern, and it was the option this decision
had to rule out rather than dismiss. It requires the layers to either use
disjoint names or agree on shared ones. Measured, they did neither: **13 names
were defined in both files, and 9 of them held a different value.**

| Name | Branded | Shipped, and canonical | Error if the wrong one won |
|---|---|---|---|
| `--text-md` | `1rem` (16px) | **13px** | 23% — on the app's *default* text size |
| `--text-lg` | `1.25rem` (20px) | **14px** | 43% |
| `--text-xl` | `1.5rem` (24px) | **16px** | 50% |
| `--text-sm` | `0.8125rem` (13px) | **12px** | 8% |
| `--text-xs` | `0.75rem` (12px) | **11px** | 9% |
| `--radius-md` | 6px | **8px** | app uses 6px three times total |
| `--radius-lg` | 8px | **12px** | |
| `--font-ui` | Urbanist / DM Sans | **`system-ui`** | no webfont is loaded by `index.html` |
| `--font-mono` | JetBrains Mono | **SF Mono** | same |

The remaining four (`--radius-sm`, `--grid-gap`, `--tile-height`,
`--tile-min-width`) agreed. CSS resolves a same-specificity collision by source
order, so a developer who linked both — or simply *read* the wrong one — got a
plausible, silently wrong number. No error, no warning.

**A second finding settled it.** The branded file was already stale against the
app it claimed to describe: its `--tile-min-width: 360px` against the 420px
`style.css` actually ships. And it was not a passive token file at all — it
carried live rules (`:focus-visible { outline: … }`), so loading it as a
"primitives layer" would have applied a global focus ring.

**Decision: superseded and deleted.** `assets/branding/tokens.css` and its
machine-readable mirror `assets/branding/tokens.json` are gone.
`assets/branding/DESIGN-SYSTEM.md` is **kept** and carries a supersession
banner: it holds the palette derivation, the measured contrast ratios, and an
unshipped light-mode design, and it is the provenance for the brand assets in
that directory — which are live and current. It is history and brand reference.
**It is not a source of CSS values.**

Nothing was lost in the deletion. The one dimension that never diverged is
colour — the palette was lifted from the brand work when the app was built, so
every colour the branded file held is already in `tokens.css` under the name
`style.css` reads (`--bg` here, `--color-bg-base` there). Of its 87 non-colliding
names, none had a call site anywhere in the app; adopting any of them would have
meant adding values nobody uses, which is the problem, not the fix.

Two divergences survive as *deliberate* choices rather than collisions:

| Dimension | Branded system | Shipped, and canonical | Why |
|---|---|---|---|
| Spacing names | `--space-1…16` (digit = px/4) | **t-shirt names** | numeric names would have collided at *different* values — a silent 2× error |
| Units | `rem` | **`px`** | §7 item 5; switching units is a real, browser-verifiable change |

The t-shirt naming is worth reading honestly: it protected `--space-*` and
nothing else. `--text-*`, `--radius-*` and the font stacks collided anyway.
**Distinct naming protects one family at a time; a single file protects all of
them.** That is why the fix is deletion and a structural guard, not a naming
convention.

---

## 4. Component vocabulary

Seven components. The list is deliberately short: a name earns its place by
being the answer to "what kind of thing is this?" for something that already
exists more than once.

### 4.1 Surface

A flat rectangle holding content: session tile, sidebar row, settings panel,
menu body.

`--bg-secondary` · `1px solid --border` · `--radius-sm` · no shadow (P2)

### 4.2 Quick link — *the reference component*

A borderless text control that reads as a link, not a button. The header and
sidebar view switchers and both sort controls are one component with four
instances.

`--accent` → `--accent-hover` on hover **and** focus-visible · `background:
transparent` and `border: none` stated **explicitly** in every state ·
`--control-pad-y` / `--control-pad-x` · `--control-gap` · `--radius-sm` ·
`--text-md` · `--t-fast` · focus ring `2px solid --accent`, offset `2px`

Read the long comment above `.quick-link` in `style.css` before touching
anything in this family. It is the best-documented decision in the codebase and
it explains why the explicit no-box guarantee is load-bearing.

### 4.3 Menu

A `<button aria-haspopup>` trigger plus a sibling `role="menu"` popup. One
mechanism (`createQuickDropdown()` in `app.js`) drives every instance; menu
*content* is per-instance.

Surface at `--radius-md` · `--shadow-popover` · `--z-popover`

The split is the project's stated rule: **content is policy, popup mechanics are
the mechanism.** A new dropdown adds a render function, never a second
open/close implementation.

### 4.4 Field

A labelled input row inside a settings panel: label, control, optional helper.

`--text-sm` label · `--text-md` control · `--space-sm` between label and control
· `--space-lg` between fields

### 4.5 Badge

A small status marker attached to something else: unread count, device tag,
attention pill.

`--text-xs` · `--radius-pill` · `--space-2xs`/`--space-xs` padding · fill only
when it carries a state (P3)

### 4.6 Panel

A full-height or bottom-anchored surface that slides over the page while the
page stays visible and usable behind it: mobile sidebar, bottom sheet, **the
agent chat panel**.

`--bg-secondary` · `--z-panel` · directional edge shadow only
(`--shadow-edge-left` for a right-side panel, `--shadow-edge-right` for a
left-side one) · `--radius-lg` on the exposed corners of a bottom sheet, square
against the edges it is anchored to

A panel is the *only* component that gets a shadow without a backdrop, because
it overlaps content while leaving it live.

### 4.7 Modal

A backdrop plus a centred dialog that takes over: settings, the send-input
confirmation gate.

Backdrop `--z-modal-backdrop`, dialog `--z-modal`, submenus within it
`--z-modal-nested` · `--radius-md` · `--shadow-modal`

---

## 5. Worked example — the agent panel

The panel is the reason this document exists, so here is the mapping in full.
Applying it is the **panel lane's** work, not this document's; every row is a
repaint and needs P7 evidence.

| Today | Reads as | Should be | Principle |
|---|---|---|---|
| `z-index: 9000` (island; nothing between 500 and 9000) | a number picked without looking | `--z-panel` (200) | §3.3 |
| Confirm gate `9500` / `9501` | same | `--z-modal-backdrop` / `--z-modal` | §3.3, 4.7 |
| Filled message bubbles | chat-app convention; a fill with no state | text on the page; sender by label/indent, not fill | P3 |
| `-4px 0 18px rgba(0,0,0,.45)` | one-off shadow | `--shadow-edge-left` (same job, named) | P2, 4.6 |
| `padding: 12px` / `10px 12px` | two families | `--space-lg`, `--control-pad-*` | P5 |
| Bubble metrics `6/8/4` | third family | `--space-sm` / `--space-md` / `--space-xs` | P1, P5 |
| Logo `22px` panel, `16px` button, `24px` wordmark | three brand sizes in one feature | one, from the type/control scale | P5 |
| `width: min(420px, 92vw)`; gate `min(380px, 92vw)` | two magic widths sharing a `92vw` | one named panel-width token | P5 |
| Branded product header inside the app | a second product | a section header, like every other panel | §1 |
| Larger type than the app around it | airy | `--text-md` (13px) | P1 |
| `z-index: 99999` inline in `chat.js` fatal banner | outside the system entirely | keep the *escape hatch*, drop the raw hex/px | P5 |

That last row is the only one with a caveat: the fatal banner fires when the
panel's own DOM is missing, so it must not depend on machinery that may be
broken. Keeping it inline is defensible — it should simply say so in a comment,
per P5.

**Note the shape of this table.** Almost every row is a *renaming*. The panel's
problem was never that its values were ugly; it is that they were arrived at
independently. That is what a design language fixes and it is all it fixes.

---

## 6. Adopting this

Incrementally, and never in one sweep. `tokens.css` changes nothing by itself —
defining a custom property has no rendering effect until a rule reads it.

**Order:**

1. ~~**Land `tokens.css`.**~~ **Done** (`89104aa`). Zero visual change. Guarded
   by `muxplex/tests/test_design_tokens.py`, which fails if any name shared with
   `style.css` resolves to a different value — so link order cannot matter.
   Superseding `assets/branding/tokens.css` was part of landing it, and is
   recorded in §3.4.
2. ~~**Link it before `style.css`**~~ **Done** — `index.html:31`/`:32`. Still zero visual change:
   `style.css`'s own `:root` wins every shared name, at an identical value.
3. **Migrate one component at a time**, cheapest first — the agent panel is the
   loudest and has its map in §5. Each migration is its own change with its own
   browser evidence.
4. **Delete `style.css`'s `:root` block** once nothing needs it as a fallback,
   leaving one home. The guard test becomes a straight assertion that every name
   `style.css` reads is one `tokens.css` defines.

**Rules while migrating:**

- One component per change. A change that touches three components cannot be
  reverted when one of them regresses.
- Never adopt a token whose value differs from the call site's current value
  without saying so and showing the pixels. `--shadow-popover` in particular
  unifies a family whose members differ slightly today.
- If a token does not exist for what you need, add it here **and** in
  `tokens.css` in the same change — never a bare value with an intention to
  tokenise it later. That is the eyeballing, arriving on schedule.

---

## 7. Open items

Recorded rather than fixed, because each is a real repaint owned by whoever owns
the affected file, and each needs P7 evidence.

1. **`--warning` and `--danger` are read but never defined.** `style.css` calls
   `var(--warning, #d29922)` and `var(--danger, #f85149)` in the compose bar,
   and `var(--danger, #c0392b)` in the follow-ups panel. Every call site renders
   its inline fallback, and **two different reds ship under one name**.
   `tokens.css` deliberately does not define them — doing so would silently
   repaint whichever sites disagree. Fix: pick one red, point the call sites at
   `--err`, delete the fallbacks. *Owner: `style.css`.*

2. **Agent panel layering (`9000/9500/9501`).** Map per §5. Verify with panel
   and settings modal open simultaneously — a stacking regression is invisible
   until exactly two things overlap. *Owner: panel lane.*

3. **`.preview-popover` at 500 sits above the settings modal at 300.** Probably
   wrong; ships today. Not silently reordered. *Owner: `style.css`.*

4. **Duplicate colour names.** `--bell-color` and `--activity-color` are
   byte-identical copies of `--bell`; `--bg-tile` has always equalled
   `--bg-secondary`. Kept as aliases at identical values — deleting a name a
   live rule reads falls back to the property's initial value, which for `color`
   means invisible text, a silent failure rather than a loud one. Migrate call
   sites, then delete. *Owner: `style.css`.*

5. **`px` throughout, not `rem`.** `rem` would respect a user's root font size —
   a genuine accessibility gain the whole app currently forgoes. It is also a
   rendered-size change for anyone not at 16px, so it cannot be smuggled in
   through a file that promises to change nothing. *Owner: a dedicated change,
   with evidence at more than one root size.*

6. **Type-scale outliers.** `15px` (7 uses), `18px`, `20px`, `28px` sit outside
   the scale. Each is either wrong or has a reason; today neither is written
   down. *Owner: `style.css`.*

7. **Control heights are not tokenised.** `--touch-min` (floor) and
   `--header-height` are defined; the resting height of a button or input is
   not, because no consistent value exists to promote yet. Measure before
   naming. *Owner: whoever next touches controls.*

8. ~~**`index.html` does not link `tokens.css`.**~~ **Done.** `index.html:31`
   links `/tokens.css` immediately before `/style.css:32` — step 2 of §6, in the
   prescribed order. Still zero visual change, because `style.css`'s own
   `:root` wins every shared name at an identical value, which is what
   `test_design_tokens.py` exists to keep true. Step 3 (migrate one component at
   a time) and step 4 (delete `style.css`'s `:root`) remain open.

---

## 8. Where things live

| File | Holds |
|---|---|
| `docs/DESIGN_LANGUAGE.md` | this — principles, components, decisions |
| `muxplex/frontend/tokens.css` | every value, with its provenance in comments |
| `muxplex/frontend/style.css` | the rules that consume them |
| `muxplex/tests/test_design_tokens.py` | the guard — keeps `tokens.css`/`style.css` from diverging, **and keeps a second token file from ever appearing** (§3.4) |
| `muxplex/frontend/deck/DESIGN_RESPONSIVE.md` | the soft deck's own spec; source of the 48×48 floor |
| `assets/branding/` | brand **assets** (SVG, icons, favicons, OG, lockup) — live and current |
| `assets/branding/DESIGN-SYSTEM.md` | **superseded** (§3.4). Brand provenance and palette derivation only — never a source of CSS values. Its `tokens.css`/`tokens.json` were deleted. |

**If you are looking for a value, there is exactly one place: `muxplex/frontend/tokens.css`.**
If it is not there, add it there — and to this document — in the same change. Do
not create a second token file; the guard test will reject it, which is the
point.

`deck/DESIGN_*.md` describe the soft deck, a different surface with different
constraints — it deliberately runs a 56px header where the PWA runs 44px, and
says so in its own stylesheet so nobody "fixes" it back. Do not reconcile them
by default; they are different products sharing a palette.

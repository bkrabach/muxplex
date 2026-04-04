# How We Built the muxplex Brand

A behind-the-scenes walkthrough of how the muxplex naming, icon, wordmark, and design system were created — from blank canvas to production-ready brand assets — using AI agents, human design judgment, and a structured creative process.

**Session:** March 26–April 1, 2026
**Tools used:** Amplifier (multi-agent orchestration), nano-banana (VLM image generation/analysis), Figma (manual vector tracing), Python (render pipeline)

---

## Step 1: The Naming Brainstorm

The project started as "web-tmux" — a working directory name with no brand identity. We needed a real name.

### Process: Parallel creative agents + round-robin distillation

Two AI design agents were dispatched **in parallel**, each independently generating name candidates:

- **Voice Strategist** (tone/messaging expert) → 15 candidates: webmux, muxdash, muxtile, panetop, muxbell, tmuxr, muxy, muxgrid, sessh, panecast, tileterm, muxsnap, bellhop, muxpop, muxlive
- **Art Director** (visual/brand expert) → 12 candidates: muxtop, muxdeck, muxboard, paneview, muxgrid, tmux-deck, **muxplex**, panecast, muxsnap, tileterm, muxbell, tmuxer

Then each agent **critiqued the other's picks** in a cross-pollination round:

> **Art Director:** *"muxplex has the most personality — you're multiplexing the multiplexer — and that self-referential nerd joke will earn a smirk in a README."*

> **COE (Crusty Old Engineer):** *"tmuxr and muxy are Tumblr-era vowel-drop tricks. Those aged. Kill them."*

This narrowed to **6 finalists**: muxdeck, panetop, tileterm, muxplex, sessh, muxbell.

**Final pick:** "muxplex" — chosen by the human after reviewing the shortlist.

### Registry validation

Before committing to the name, we checked availability across all major package registries:

| Registry | Status |
|----------|--------|
| npm | Available |
| PyPI | Available |
| crates.io | Available |
| Homebrew | Available |
| Docker Hub | Available |
| GitHub | 2 abandoned repos (0 stars, inactive since 2023) — no conflict |

---

## Step 2: Icon Exploration (AI-Generated Rounds)

### Round 1: Four initial concepts

Using nano-banana (Gemini VLM), we generated four icon concepts:

| Concept | Description |
|---------|-------------|
| **Grid** | 3×3 terminal pane grid, amber cursors, notification dot |
| **Hub** | MX monogram with radial lines (rendered as "MLX" — AI got creative) |
| **Wordmark-Split** | "mux" white + "plex" cyan with amber cursor — strong two-tone |
| **Badge** | "mx" monogram on blueprint grid with amber badge |

### Round 2: Refinement (Icons C through I)

More variations were generated and scored by the Art Director agent on four dimensions:

| Criterion | Weight |
|-----------|--------|
| Color harmony with brand palette | 10 |
| Style match (geometric, technical) | 10 |
| Size scaling at 32px (favicon) | 10 |
| Overall brand coherence | 10 |

**Winner: Icon D (Browser Frame) — 38/40.** A browser window frame with a 2×2 terminal grid and traffic-light dots (amber, white, cyan). Used all four brand colors and scaled well to favicon size.

**Runner-up: Icon C (Cursor) — 36/40.** Single terminal with `#_` prompt. Better monospace fit but missed the "web" and "multi-session" signals.

### The human touch: Figma tracing

The AI-generated icon concepts were good directionally but lacked the precision needed for production use. The user took the winning concept and **manually traced it in Figma**, creating a clean vector with:

- Compound-path browser frame (outer rounded rect + header cutout)
- Three traffic-light circles: amber (#F1A640), white, cyan (#09C8E5)
- 2×2 grid of rounded pane rectangles (#222433)
- Background fill (#10131C)

This Figma SVG became the canonical icon source.

---

## Step 3: Typography — Finding the Font

The initial AI-generated wordmark used a proportional geometric sans-serif that wasn't any specific font. We needed to identify (or match) it for production use.

### Process: VLM font matching across 1,600+ Google Fonts

Nano-banana's vision analysis described the original letterforms:
- Proportional (not fixed-width)
- Geometric, monolinear
- Spurless arches on m/u
- Flat-cut terminals

A comparison page was generated with 8 candidate Google Fonts: Poppins, Josefin Sans, **Urbanist**, Outfit, DM Sans, Plus Jakarta Sans, Raleway, Jura.

**Vision AI analysis identified Urbanist 700 as the winner:**

> *"The tail-less `u` with flat horizontal terminals is the single most discriminating feature — it immediately eliminates Poppins, DM Sans, and others. Urbanist is the only font in the candidate list that shares this construction."*

**Font stack established:**
- **Urbanist 700** — Wordmark only (restricted use)
- **DM Sans 400/500/600** — All UI text
- **JetBrains Mono 400/500** — Terminal content, code blocks

---

## Step 4: The Complete SVG Brand Set

With the Figma-traced icon and Urbanist 700 identified, the full SVG brand set was created programmatically:

### Wordmark SVGs (outlined Urbanist letterforms — no font dependency)
- `wordmark-on-dark.svg` — "mux" (#F0F6FF) + "plex" (#00D9F5), transparent
- `wordmark-on-light.svg` — "mux" (#0D1117) + "plex" (#0090B0), transparent

### Icon SVGs (scaled from Figma trace)
- `muxplex-icon-dark.svg` — White frame on dark inner bg
- `muxplex-icon-light.svg` — Dark frame on light inner bg

### Lockup SVGs (icon + wordmark combined)
- `lockup-on-dark.svg` — Icon left + wordmark right
- `lockup-on-light.svg` — Same, light variant

### Iteration highlights
- **Multiple rounds of spacing/alignment** between icon and wordmark
- **Cropping refinement** — trimmed excessive whitespace around the lockup
- **Color matching** — ensured the cyan in the icon matched "plex" letterforms
- **Background handling** — light icon variant got subtle gray inner boxes for contrast

---

## Step 5: The Color Palette

| Token | Hex | Use |
|-------|-----|-----|
| `--bg` | `#0D1117` | Primary dark background |
| `--bg-secondary` | `#10131C` | Icon card background |
| `--text` | `#F0F6FF` | Primary text, "mux" wordmark |
| `--accent` | `#00D9F5` | Brand cyan, "plex" wordmark |
| `--bell-color` | `#F1A640` | Amber accent, notification indicators |
| `--bg-surface` | `#161B22` | Card/panel backgrounds |
| `--border` | `#30363D` | Subtle borders |

---

## Step 6: The Render Pipeline

A 438-line Python script (`scripts/render-brand-assets.py`) generates all production assets from the SVG sources:

**Renderer auto-detection chain:** cairosvg → rsvg-convert → inkscape → imagemagick

**Generated assets:**
- App icons: 16, 22, 32, 44, 48, 64, 128, 256, 512, 1024px
- Favicons: favicon.ico (multi-size), favicon-32.png, apple-touch-icon (180px)
- PWA icons: 192px, 512px
- Wordmark PNGs: 32px and 64px height
- Lockup PNGs: 32px and 64px height
- OG images: 1200×630 (dark + light variants)
- Platform-specific: .icns (macOS), .ico (Windows)

Run with: `python scripts/render-brand-assets.py`

---

## Step 7: The Design System

A comprehensive 857-line design specification (`assets/branding/DESIGN-SYSTEM.md`) was created covering:

1. **Color System** — 4-tier background luminance, WCAG contrast ratios
2. **Typography** — 3 font stacks with precise weight/size scales
3. **Spacing** — 4px base unit, 8 scale stops
4. **Border Radius** — Sharp terminals, soft UI chrome
5. **Motion** — 4 duration tokens, 4 easing curves, GPU rules
6. **Z-Index** — 8-layer stacking system
7. **Component Patterns** — Tile, header, terminal, bell badge specs
8. **Accessibility** — WCAG 2.1 AA, focus indicators, touch targets
9. **Do/Don't Reference** — Anti-patterns to avoid

Plus machine-readable tokens:
- `tokens.json` — W3C Design Tokens format
- `tokens.css` — CSS custom properties

---

## The Agent Roster

| Agent | Role | Contribution |
|-------|------|-------------|
| `design-intelligence:voice-strategist` | Creative | 15 naming candidates, shortlist critique |
| `design-intelligence:art-director` | Creative | 12 naming candidates, icon brand-compatibility scoring (38/40) |
| `design-intelligence:component-designer` | Creative | Custom scrollbar styling, UI component guidance |
| `design-intelligence:layout-architect` | Reasoning | Settings panel layout, grid architecture |
| `design-intelligence:responsive-strategist` | Reasoning | Mobile strategy, breakpoints, FAB placement |
| `crusty-old-engineer` (skill) | Advisory | Name pruning, architecture skepticism |
| `nano-banana` (tool) | Vision + Generation | 8+ icon generation rounds, font identification |
| `foundation:web-research` | Research | Registry availability checks |
| Human (Figma) | Manual design | Final icon vector tracing |

---

## Final Asset Tree

```
assets/branding/
├── DESIGN-SYSTEM.md          # 857-line design specification
├── README.md                 # Brand asset usage guide
├── tokens.json               # W3C design tokens
├── tokens.css                # CSS custom properties
├── svg/
│   ├── icon/
│   │   ├── muxplex-icon-dark.svg
│   │   └── muxplex-icon-light.svg
│   ├── wordmark/
│   │   ├── wordmark-on-dark.svg
│   │   └── wordmark-on-light.svg
│   └── lockup/
│       ├── lockup-on-dark.svg
│       └── lockup-on-light.svg
├── icons/                    # 16–1024px PNGs
├── favicons/                 # .ico, apple-touch-icon
├── pwa/                      # 192, 512px
├── lockup/                   # 32, 64px PNGs
└── og/                       # 1200×630 Open Graph images

scripts/
└── render-brand-assets.py    # 438-line multi-renderer pipeline
```

---

## The Arc in One Sentence

The muxplex brand was born from a parallel round-robin brainstorm between two creative AI agents, validated across 6 package registries, visually iterated through 8+ rounds of AI icon generation scored by an art director agent, had its font identified by vision AI (Urbanist 700), was hand-traced by the user in Figma for precision, and was codified into an 857-line design system with full dark/light mode support, WCAG AA compliance, and a 438-line Python render pipeline — all within a single multi-day session.

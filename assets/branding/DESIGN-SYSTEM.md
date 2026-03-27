# muxplex Design System

**Version:** 1.0.0
**Date:** 2026-03-27
**Applies to:** muxplex web-based tmux session dashboard

This document defines every visual property used in muxplex. It is the single
source of truth. All values reference `tokens.css` (CSS custom properties) and
`tokens.json` (structured data for tooling).

---

## How to Use This File

1. Add `<link rel="stylesheet" href="tokens.css">` before your app styles.
2. Reference any token as `var(--color-bg-base)`, `var(--space-4)`, etc.
3. This document explains *when* and *why* to use each token.
4. The JSON file mirrors every value for scripts, linters, or framework config.

---

## Table of Contents

1. [Color System](#1-color-system)
2. [Typography](#2-typography)
3. [Spacing](#3-spacing)
4. [Border Radius](#4-border-radius)
5. [Shadows & Elevation](#5-shadows--elevation)
6. [Motion & Transitions](#6-motion--transitions)
7. [Z-Index Layers](#7-z-index-layers)
8. [Component Patterns](#8-component-patterns)
9. [Dark / Light Mode](#9-dark--light-mode)
10. [Accessibility](#10-accessibility)
11. [Do / Don't Reference](#11-do--dont-reference)

---

## 1. Color System

### 1.1 Backgrounds

Dark mode uses **luminance stepping** — lighter surfaces appear more elevated.
This is the primary depth cue; shadows are supplementary.

| Token                    | Dark        | Light       | Purpose                           |
|--------------------------|-------------|-------------|-----------------------------------|
| `--color-bg-base`        | `#0D1117`   | `#FFFFFF`   | Page background, deepest layer    |
| `--color-bg-elevated`    | `#10131C`   | `#F0F0F0`   | Cards, panels, tile bodies        |
| `--color-bg-surface`     | `#1A1F2B`   | `#E8E9EE`   | Raised surfaces, hover states     |
| `--color-bg-muted`       | `#222433`   | `#DADCE0`   | Subtle fills, secondary panels    |

**Usage rules:**
- `bg-base` is always the page `<body>` background.
- `bg-elevated` is the default card/panel surface (session tiles, picker).
- `bg-surface` appears on hover or as a secondary tier within a card (tile header).
- `bg-muted` is for de-emphasized areas (idle session backgrounds, code blocks).

### 1.2 Text

| Token                      | Dark        | Light       | Contrast on bg-base | Purpose                  |
|----------------------------|-------------|-------------|---------------------|--------------------------|
| `--color-text-primary`     | `#F0F6FF`   | `#0D1117`   | 17.4:1 / 18.9:1 AAA | Headings, body text     |
| `--color-text-secondary`   | `#8E95A3`   | `#4A5060`   | 6.3:1 / 8.1:1 AA    | Labels, timestamps       |
| `--color-text-disabled`    | `#4A5060`   | `#8E95A3`   | 2.4:1 / 2.6:1       | Disabled controls only   |

**Usage rules:**
- `text-primary` for anything the user needs to read — session names, body copy,
  headings, wordmark text.
- `text-secondary` for supporting information — "2s ago" timestamps, hint text,
  metadata labels, picker keyboard shortcuts.
- `text-disabled` **only** for controls the user cannot interact with. Never for
  content a sighted user needs to read. The low contrast is intentional — it
  signals "you can't use this."

### 1.3 Accents

| Token                    | Dark        | Light       | Role                              |
|--------------------------|-------------|-------------|-----------------------------------|
| `--color-accent-cyan`    | `#00D9F5`   | `#007D8C`   | Primary accent, links, "plex"     |
| `--color-accent-amber`   | `#F1A640`   | `#946000`   | Notifications, bell indicator     |

**Critical note — light mode:** The brand cyan (`#00D9F5`) fails contrast on
white (1.7:1). In light mode, the token automatically maps to `#007D8C` (4.9:1
AA) for text. If you need the bright cyan for a decorative non-text element in
light mode, use the literal hex `#00D9F5` directly — but never for text.

The same applies to amber: `#946000` replaces `#F1A640` in light mode for text
use (5.3:1 AA).

### 1.4 Borders

| Token                     | Dark        | Light       | Purpose                      |
|---------------------------|-------------|-------------|------------------------------|
| `--color-border-subtle`   | `#1E2430`   | `#E0E2E8`   | Dividers within a surface    |
| `--color-border-default`  | `#2A3040`   | `#D0D2D8`   | Card outlines, input borders |
| `--color-border-strong`   | `#3A4050`   | `#B0B4BC`   | Active outlines, emphasis    |

**Usage rules:**
- Borders in dark mode are **decorative**, not functional separators. Don't rely
  on border visibility for meaning — use background contrast instead.
- `border-subtle` for hairline dividers inside cards (e.g., between tile header
  and terminal content).
- `border-default` for card outlines and input field borders.
- `border-strong` for active/focused input borders or highlighted tiles.

### 1.5 Semantic State Colors

| Token              | Dark        | Light       | Contrast (dark bg-base) | Purpose            |
|--------------------|-------------|-------------|-------------------------|--------------------|
| `--color-success`  | `#3FB950`   | `#1A7F37`   | 7.5:1 AAA               | Connected, healthy |
| `--color-error`    | `#F85149`   | `#CF222E`   | 5.7:1 AA                | Disconnected, fail |
| `--color-warning`  | `#F1A640`   | `#946000`   | 9.3:1 AAA               | Attention needed   |
| `--color-info`     | `#58A6FF`   | `#0969DA`   | 7.5:1 AAA               | Informational      |

Each state has a background tint variant (`--color-success-bg`, etc.) at ~10-12%
opacity for banner or badge backgrounds.

### 1.6 Overlays

| Token               | Dark                         | Light                          | Purpose              |
|----------------------|------------------------------|--------------------------------|----------------------|
| `--color-backdrop`   | `rgba(0, 0, 0, 0.60)`       | `rgba(0, 0, 0, 0.30)`         | Behind picker/modal  |
| `--color-scrim`      | `rgba(13, 17, 23, 0.80)`    | `rgba(255, 255, 255, 0.80)`   | Frosted glass areas  |

---

## 2. Typography

### 2.1 Font Stacks

```css
--font-display:  'Urbanist', system-ui, -apple-system, sans-serif;
--font-ui:       'DM Sans', 'Inter', system-ui, -apple-system, sans-serif;
--font-mono:     'JetBrains Mono', 'Fira Code', 'Cascadia Code', 'Menlo', monospace;
```

**Load from Google Fonts:**
```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400;500&family=Urbanist:wght@700&display=swap" rel="stylesheet">
```

### 2.2 Font Roles

| Font            | Weights     | Use Cases                                            |
|-----------------|-------------|------------------------------------------------------|
| Urbanist        | 700         | Wordmark only, H1-level page titles                  |
| DM Sans         | 400 / 500 / 600 | All UI text — buttons, labels, body, headings H2+ |
| JetBrains Mono  | 400 / 500   | Terminal tile content, expanded terminal, code blocks |

**Urbanist is restricted.** It appears in exactly two places:
1. The "muxplex" wordmark in the app bar.
2. An H1 page title if the app ever has one (currently: "Terminal Dashboard" in
   the app bar could use it, or stay DM Sans 600 for a quieter look).

Do not use Urbanist for buttons, labels, or body text. Its high x-height and
geometric structure read as a display face, not a UI face.

### 2.3 Type Scale

All sizes use `--font-ui` (DM Sans) unless noted.

| Token          | Size   | Weight           | Line-height          | Use                                  |
|----------------|--------|------------------|----------------------|--------------------------------------|
| `--text-xs`    | 12px   | 400 regular      | `--leading-base` 1.5 | Fine print, keyboard shortcuts       |
| `--text-sm`    | 13px   | 400 / 500        | `--leading-base` 1.5 | Timestamps, secondary labels         |
| `--text-base`  | 14px   | 400 / 500        | `--leading-base` 1.5 | **Default UI text** — buttons, body  |
| `--text-md`    | 16px   | 500 medium       | `--leading-snug` 1.375 | Session names in tiles              |
| `--text-lg`    | 20px   | 600 semibold     | `--leading-tight` 1.2 | Section headings                    |
| `--text-xl`    | 24px   | 600 semibold     | `--leading-tight` 1.2 | Panel titles                        |
| `--text-2xl`   | 30px   | 700 Urbanist     | `--leading-tight` 1.2 | H1 page titles                      |
| `--text-3xl`   | 36px   | 700 Urbanist     | `--leading-none` 1.0 | Hero wordmark (if needed)            |

### 2.4 Letter Spacing

| Token               | Value      | Use                                         |
|----------------------|------------|---------------------------------------------|
| `--tracking-tight`  | -0.01em    | Headings 20px+ (tighten for optical balance)|
| `--tracking-normal`  | 0          | Body text, default                          |
| `--tracking-wide`   | 0.02em     | Small text <13px (open up for legibility)   |
| `--tracking-wider`  | 0.04em     | All-caps labels ("NEEDS ATTENTION")         |

### 2.5 Terminal Typography

| Context            | Font          | Size                      | Weight |
|--------------------|---------------|---------------------------|--------|
| Tile preview       | JetBrains Mono | `--tile-font-size` 10.5px | 400    |
| Expanded terminal  | JetBrains Mono | `--terminal-font-size` 14px | 400  |
| Code in UI         | JetBrains Mono | `--text-sm` 13px          | 400    |

- Terminal text uses the standard ANSI 16-color palette provided by xterm.js.
  Don't override ANSI colors with brand colors.
- In overview tiles, 10.5px monospace yields ~50 characters per 360px tile width —
  enough to scan build output, log lines, and command prompts.
- The expanded terminal at 14px matches standard terminal emulator defaults.

---

## 3. Spacing

### 3.1 Scale

4px base unit. Every spacing value is a multiple of 4.

| Token       | Value | Pixels | Common Use                                  |
|-------------|-------|--------|---------------------------------------------|
| `--space-1` | 0.25rem | 4px  | Inner padding of small elements, icon gaps  |
| `--space-2` | 0.5rem  | 8px  | Grid gap, compact padding, inline spacing   |
| `--space-3` | 0.75rem | 12px | Card inner padding, button padding          |
| `--space-4` | 1rem    | 16px | Page margin, section gaps                   |
| `--space-6` | 1.5rem  | 24px | Between groups of content                   |
| `--space-8` | 2rem    | 32px | Major section separation                    |
| `--space-12`| 3rem    | 48px | App bar height equivalent, hero spacing     |
| `--space-16`| 4rem    | 64px | Maximum breathing room                      |

### 3.2 Layout Constants

These are fixed dimensions drawn from the layout architecture spec.

| Token                       | Value  | Purpose                              |
|-----------------------------|--------|--------------------------------------|
| `--tile-min-width`          | 360px  | CSS Grid auto-fill minmax floor      |
| `--tile-height`             | 300px  | Fixed tile height for scanning       |
| `--grid-gap`                | 8px    | Space between tiles                  |
| `--page-margin`             | 16px   | Inset from viewport edges            |
| `--app-bar-height`          | 48px   | Overview mode header                 |
| `--status-bar-height`       | 36px   | Expanded mode header                 |
| `--tile-header-height`      | 28px   | Session name row in each tile        |
| `--picker-width`            | 400px  | Command palette panel width          |
| `--picker-row-height`       | 36px   | Desktop session row                  |
| `--picker-row-height-touch` | 48px   | Touch-device session row (44px min)  |
| `--breakpoint-list`         | 640px  | Below this: list view, not grid      |

### 3.3 Grid Setup

```css
.session-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(var(--tile-min-width), 1fr));
  gap: var(--grid-gap);
  padding: var(--page-margin);
}
```

This one rule handles responsive column count from 320px to 2560px+ viewports.
No breakpoints needed for column count — `auto-fill` adapts naturally.

---

## 4. Border Radius

| Token           | Value    | Use                                           |
|-----------------|----------|-----------------------------------------------|
| `--radius-none` | 0        | Terminal content areas (hard edges = terminal) |
| `--radius-sm`   | 4px      | Small buttons, input fields, badges           |
| `--radius-md`   | 6px      | Session tiles, cards, panels                  |
| `--radius-lg`   | 8px      | Modal dialogs, command palette                |
| `--radius-xl`   | 12px     | Large cards, image containers                 |
| `--radius-full` | 9999px   | Pills, dots, avatar circles                   |

**Guiding principle:** Terminal-adjacent elements use sharper radii (0–4px).
UI chrome uses softer radii (6–12px). The contrast between sharp terminal
content and softened UI chrome reinforces the "tool wrapping a terminal" feel.

---

## 5. Shadows & Elevation

### 5.1 Dark Mode

Primary depth cue is **luminance stepping** (bg-base < bg-elevated < bg-surface <
bg-muted). Shadows are secondary — used mainly on floating overlays.

| Token          | Value                                                     | Use                          |
|----------------|-----------------------------------------------------------|------------------------------|
| `--shadow-none`| `none`                                                    | Default (no shadow needed)   |
| `--shadow-sm`  | `0 1px 2px rgba(0,0,0,0.30)`                             | Subtle lift on hover         |
| `--shadow-md`  | `0 2px 8px rgba(0,0,0,0.35), 0 1px 2px rgba(0,0,0,0.20)`| Cards, dropdown menus        |
| `--shadow-lg`  | `0 4px 16px rgba(0,0,0,0.40), 0 2px 4px rgba(0,0,0,0.25)`| Picker panel, modals       |
| `--shadow-xl`  | `0 8px 32px rgba(0,0,0,0.50), 0 4px 8px rgba(0,0,0,0.30)`| Expanded tile during zoom  |

### 5.2 Glow Effects

| Token           | Value                                 | Use                        |
|-----------------|---------------------------------------|----------------------------|
| `--glow-cyan`   | `0 0 8px rgba(0,217,245,0.35)`       | Focus ring halo, active UI |
| `--glow-amber`  | `0 0 8px rgba(241,166,64,0.40)`      | Bell indicator pulse       |

### 5.3 Light Mode

Shadows become the primary depth cue in light mode. Opacity values are lower
(0.04–0.14) since dark shadows on light surfaces are naturally more visible.
Token names stay the same — values switch automatically.

---

## 6. Motion & Transitions

### 6.1 Duration Tokens

| Token                  | Value  | Use                               |
|------------------------|--------|-----------------------------------|
| `--duration-instant`   | 75ms   | Hover color shifts                |
| `--duration-fast`      | 150ms  | Button press, toggle, focus ring  |
| `--duration-moderate`  | 250ms  | Tile zoom, panel slide, picker    |
| `--duration-slow`      | 400ms  | Page-level transitions            |

### 6.2 Easing Functions

| Token           | Curve                              | Feel                           |
|-----------------|------------------------------------|--------------------------------|
| `--ease-out`    | `cubic-bezier(0.16, 1, 0.3, 1)`   | Decelerating — tile zoom in    |
| `--ease-in-out` | `cubic-bezier(0.45, 0, 0.55, 1)`  | Symmetric — tile zoom out      |
| `--ease-spring` | `cubic-bezier(0.34, 1.56, 0.64, 1)` | Overshoot — picker pop-in    |
| `--ease-linear` | `linear`                           | Progress bars, continuous      |

### 6.3 Convenience Shorthands

```css
/* Color transitions — use on interactive elements */
transition: var(--transition-colors);

/* Opacity transitions — use on fade in/out */
transition: var(--transition-opacity);

/* Transform transitions — use on position/scale changes */
transition: var(--transition-transform);
```

### 6.4 GPU Acceleration Rule

Only animate `transform` and `opacity`. These are composited on the GPU and
maintain 60fps. Never animate `width`, `height`, `top`, `left`, `margin`, or
`padding` — these trigger layout recalculation.

**Example — tile zoom:**
```css
.tile--expanding {
  /* Good: GPU-composited */
  transform: scale(1.5) translate(100px, 50px);
  transition: transform var(--duration-moderate) var(--ease-out);
}

/* Bad: triggers layout */
.tile--expanding-bad {
  width: 100vw;
  height: 100vh;
  transition: width 250ms, height 250ms;
}
```

### 6.5 Reduced Motion

All duration tokens collapse to `0ms` when `prefers-reduced-motion: reduce` is
active. This means transitions become instantaneous — no motion, but the state
change still happens. No additional code is needed if you use the tokens.

---

## 7. Z-Index Layers

| Token            | Value | Layer                              |
|------------------|-------|------------------------------------|
| `--z-base`       | 0     | Default stacking context           |
| `--z-tile`       | 1     | Session tiles in grid              |
| `--z-app-bar`    | 10    | App bar / status bar               |
| `--z-expanding`  | 20    | Tile during zoom animation         |
| `--z-backdrop`   | 30    | Semi-transparent overlay backdrop   |
| `--z-picker`     | 40    | Command palette panel              |
| `--z-toast`      | 50    | Toast notifications                |
| `--z-tooltip`    | 60    | Tooltips (always on top)           |

**Rule:** Never use raw z-index numbers. Always reference the token. This
prevents z-index wars where developers guess higher and higher values.

---

## 8. Component Patterns

### 8.1 Session Tile Card

The fundamental UI element. A fixed-height card showing a tmux session preview.

```
┌──────────────────────────────────────────┐
│ ● session-name                    2s ago │  ← Header: 28px
├──────────────────────────────────────────┤
│                                          │
│  $ npm run build                         │
│  > project@1.0.0 build                   │
│  > tsc && vite build                     │  ← Terminal content
│  ...                                     │     JetBrains Mono 10.5px
│  ✓ built in 3.42s                        │     on bg-elevated
│                                          │
└──────────────────────────────────────────┘
```

**Structure:**

```css
.session-tile {
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-md);            /* 6px */
  height: var(--tile-height);                 /* 300px */
  overflow: hidden;
  display: flex;
  flex-direction: column;
  transition: var(--transition-colors);
}

.session-tile:hover {
  background: var(--color-bg-surface);
  border-color: var(--color-border-default);
}

.session-tile:focus-visible {
  outline: 2px solid var(--color-focus-ring);
  outline-offset: 2px;
}

.tile-header {
  height: var(--tile-header-height);          /* 28px */
  padding: 0 var(--space-2);                  /* 0 8px */
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--color-bg-surface);
  border-bottom: 1px solid var(--color-border-subtle);
}

.tile-name {
  font-family: var(--font-ui);
  font-size: var(--text-md);                  /* 16px */
  font-weight: var(--weight-medium);          /* 500 */
  color: var(--color-text-primary);
  line-height: var(--leading-snug);
}

.tile-timestamp {
  font-family: var(--font-ui);
  font-size: var(--text-xs);                  /* 12px */
  color: var(--color-text-secondary);
}

.tile-terminal {
  flex: 1;
  padding: var(--space-1) var(--space-2);     /* 4px 8px */
  font-family: var(--font-mono);
  font-size: var(--tile-font-size);           /* 10.5px */
  line-height: var(--leading-snug);           /* 1.375 */
  color: var(--color-text-primary);
  overflow: hidden;
  white-space: pre;
  /* Fade overflow at top — show most recent lines */
  mask-image: linear-gradient(to bottom, transparent 0%, black 24px);
  -webkit-mask-image: linear-gradient(to bottom, transparent 0%, black 24px);
}
```

### 8.2 Header / App Bar

```css
.app-bar {
  height: var(--app-bar-height);              /* 48px */
  padding: 0 var(--page-margin);              /* 0 16px */
  display: flex;
  align-items: center;
  gap: var(--space-3);                        /* 12px */
  background: var(--color-bg-surface);
  border-bottom: 1px solid var(--color-border-subtle);
  z-index: var(--z-app-bar);
}

/* Expanded mode — thinner */
.status-bar {
  height: var(--status-bar-height);           /* 36px */
  padding: 0 var(--space-3);
  display: flex;
  align-items: center;
  gap: var(--space-2);
  background: var(--color-bg-surface);
  border-bottom: 1px solid var(--color-border-subtle);
  z-index: var(--z-app-bar);
}

.app-title {
  font-family: var(--font-display);           /* Urbanist */
  font-weight: var(--weight-bold);            /* 700 */
  font-size: var(--text-lg);                  /* 20px */
  letter-spacing: var(--tracking-tight);
  color: var(--color-text-primary);
}

/* Wordmark coloring:  mux = primary text,  plex = cyan accent */
.wordmark-mux  { color: var(--color-text-primary); }
.wordmark-plex { color: var(--color-accent-cyan); }
```

### 8.3 Terminal Container (Expanded Mode)

```css
.terminal-container {
  flex: 1;
  background: var(--color-bg-base);           /* Deepest black */
  /* xterm.js mounts into this element */
  /* Sized via ResizeObserver — no fixed dimensions */
}

/* xterm.js configuration tokens */
.xterm-config {
  --xterm-font-family: var(--font-mono);
  --xterm-font-size: var(--terminal-font-size);  /* 14px */
  --xterm-bg: var(--color-bg-base);
  --xterm-fg: var(--color-text-primary);
  --xterm-cursor: var(--color-accent-cyan);
}
```

### 8.4 Bell Badge / Activity Indicator

The bell is the only element that should interrupt the user's scanning pattern.

```css
/* Dot indicator in tile header */
.bell-dot {
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full);
  background: var(--color-accent-amber);
  flex-shrink: 0;
}

/* Idle state — no dot, or dim neutral */
.bell-dot--idle {
  background: var(--color-text-disabled);
}

/* Active bell — amber with glow pulse */
.bell-dot--active {
  background: var(--color-accent-amber);
  box-shadow: var(--glow-amber);
  animation: bell-pulse 2s ease-in-out infinite;
}

@keyframes bell-pulse {
  0%, 100% { opacity: 1; }
  50%      { opacity: 0.6; }
}

@media (prefers-reduced-motion: reduce) {
  .bell-dot--active {
    animation: none;
    /* Static amber dot — still visible, just not moving */
  }
}

/* Border glow on the whole tile when bell is active */
.session-tile--bell {
  border-color: var(--color-accent-amber);
  box-shadow: inset 0 0 0 1px var(--color-accent-amber),
              var(--glow-amber);
}
```

### 8.5 Command Palette / Session Picker

```css
.picker-backdrop {
  position: fixed;
  inset: 0;
  background: var(--color-backdrop);
  z-index: var(--z-backdrop);
}

.picker-panel {
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: var(--picker-width);                 /* 400px */
  max-height: 70vh;
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-lg);            /* 8px */
  box-shadow: var(--shadow-lg);
  z-index: var(--z-picker);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.picker-row {
  height: var(--picker-row-height);           /* 36px */
  padding: 0 var(--space-3);
  display: flex;
  align-items: center;
  gap: var(--space-2);
  cursor: pointer;
  transition: var(--transition-colors);
}

.picker-row:hover,
.picker-row--active {
  background: var(--color-bg-muted);
}

.picker-hint {
  font-size: var(--text-xs);
  color: var(--color-text-secondary);
  letter-spacing: var(--tracking-wide);
}

/* Mobile: bottom sheet */
@media (max-width: 639px) {
  .picker-panel {
    top: auto;
    bottom: 0;
    left: 0;
    right: 0;
    transform: none;
    width: 100%;
    max-height: 60vh;
    border-radius: var(--radius-xl) var(--radius-xl) 0 0;
  }

  .picker-row {
    height: var(--picker-row-height-touch);   /* 48px */
  }
}
```

### 8.6 Status Indicator (Connection)

```css
.status-dot {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
}

.status-dot--connected {
  background: var(--color-success);
}

.status-dot--disconnected {
  background: var(--color-error);
}

.status-dot--connecting {
  background: var(--color-warning);
  animation: blink 1.5s ease-in-out infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50%      { opacity: 0.3; }
}

@media (prefers-reduced-motion: reduce) {
  .status-dot--connecting {
    animation: none;
  }
}
```

---

## 9. Dark / Light Mode

### 9.1 How It Works

`tokens.css` defines dark mode as the default (`:root`). Light mode activates in
two ways:

1. **Automatic:** `@media (prefers-color-scheme: light)` overrides all tokens.
2. **Manual:** Add `data-theme="light"` to `<html>` for user toggle.

The `data-theme` attribute always wins. If a user sets `data-theme="dark"`, they
stay dark even if their OS is in light mode.

### 9.2 Implementation

```js
// Read user preference or fall back to OS
function getTheme() {
  const stored = localStorage.getItem('muxplex-theme');
  if (stored) return stored;                        // 'light' | 'dark'
  return matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

// Apply
function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('muxplex-theme', theme);
}

// Toggle
function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme') || getTheme();
  applyTheme(current === 'dark' ? 'light' : 'dark');
}
```

### 9.3 What Changes, What Doesn't

| Property       | Changes between modes? | Notes                                   |
|----------------|------------------------|-----------------------------------------|
| Background     | Yes                    | Full palette swap                        |
| Text           | Yes                    | Full palette swap                        |
| Accents        | Yes                    | Darkened for light mode contrast         |
| Borders        | Yes                    | Full palette swap                        |
| State colors   | Yes                    | Darkened for light mode contrast         |
| Shadows        | Yes                    | Lower opacity in light mode              |
| Typography     | No                     | Same fonts, sizes, weights               |
| Spacing        | No                     | Same layout constants                    |
| Border radius  | No                     | Same values                              |
| Motion         | No                     | Same durations and easing                |
| Z-index        | No                     | Same stacking                            |

---

## 10. Accessibility

### 10.1 Contrast Compliance

All text-on-background combinations meet WCAG 2.1 AA (4.5:1 for normal text,
3:1 for large text):

| Pairing                           | Dark   | Light  | Level |
|-----------------------------------|--------|--------|-------|
| text-primary on bg-base           | 17.4:1 | 18.9:1 | AAA   |
| text-primary on bg-elevated       | 17.1:1 | 16.6:1 | AAA   |
| text-primary on bg-surface        | 15.2:1 | 15.6:1 | AAA   |
| text-secondary on bg-base         | 6.3:1  | 8.1:1  | AA    |
| text-secondary on bg-surface      | 5.5:1  | 6.6:1  | AA    |
| accent-cyan on bg-base            | 11.0:1 | 4.9:1  | AA+   |
| accent-amber on bg-base           | 9.3:1  | 5.3:1  | AA    |

**Exception:** `text-disabled` intentionally fails contrast (2.4:1). It is used
only on non-interactive disabled elements where the low contrast communicates
"unavailable." This follows WCAG 2.1 §1.4.3 exception for disabled components.

### 10.2 Focus Indicators

Every interactive element gets a visible focus ring via `:focus-visible`:

```css
:focus-visible {
  outline: 2px solid var(--color-focus-ring);
  outline-offset: 2px;
}
```

The cyan focus ring achieves 11.0:1 contrast on dark backgrounds and 4.9:1 on
light — well above the 3:1 WCAG requirement for UI components.

### 10.3 Touch Targets

- Picker rows on mobile: 48px tall (≥44px WCAG minimum).
- Session tiles: full card is the click target — well above 44px.
- Settings gear and close buttons: minimum 44×44px touch area (can be visually
  smaller with padding extending the target).

### 10.4 Reduced Motion

All `--duration-*` tokens become `0ms` under `prefers-reduced-motion: reduce`.
Animations declared with `@keyframes` should include an explicit
`@media (prefers-reduced-motion: reduce)` block that sets `animation: none`.

### 10.5 Screen Reader Support

Reference the semantic HTML structure in the layout spec. Key points:
- Grid uses `role="grid"` with `role="gridcell"` per tile.
- Each tile has `aria-label` combining session name, bell state, and timestamp.
- Expanded terminal uses `role="application"` (keyboard goes to xterm.js).
- Skip link: "Skip to sessions" at page top.

---

## 11. Do / Don't Reference

### Color

| Do | Don't |
|----|-------|
| Use `var(--color-text-primary)` for all readable text | Use `text-disabled` for text users need to read |
| Use semantic tokens (`--color-error`) for status meaning | Use accent-amber for errors (amber = attention, not failure) |
| Use `--color-bg-surface` for hover states | Create new hex colors outside the token system |
| Let accents auto-adjust between dark/light mode | Hardcode `#00D9F5` in light mode (fails contrast) |

### Typography

| Do | Don't |
|----|-------|
| Use Urbanist 700 only for wordmark and H1 titles | Use Urbanist for buttons, labels, or body text |
| Use DM Sans for all UI text (buttons, labels, body) | Mix Inter and DM Sans — pick one (DM Sans primary) |
| Use JetBrains Mono only for terminal/code content | Use JetBrains Mono for UI labels or headings |
| Use `--tracking-wider` for all-caps section labels | Set letter-spacing to 0 on all-caps text (looks cramped) |

### Spacing

| Do | Don't |
|----|-------|
| Use `--space-*` tokens for all spacing | Use arbitrary values (15px, 7px, 23px) |
| Use `--grid-gap` (8px) between tiles | Use larger gaps — this is a dense monitoring tool |
| Use `--page-margin` (16px) for viewport inset | Let tiles touch viewport edges |
| Use `--space-2` (8px) for compact inline gaps | Use 0 gap between inline elements |

### Borders & Radius

| Do | Don't |
|----|-------|
| Use `--radius-md` (6px) for cards and tiles | Use large radii (16px+) — this is a developer tool |
| Use `--radius-none` (0) on terminal content areas | Round terminal corners (terminals have hard edges) |
| Use `--border-subtle` inside cards, `--border-default` on card edges | Use borders as primary separators — background contrast is primary |

### Motion

| Do | Don't |
|----|-------|
| Animate only `transform` and `opacity` | Animate `width`, `height`, `top`, `left`, `margin` |
| Use `--ease-out` for zoom in, `--ease-in-out` for zoom out | Use `ease` (default) or `linear` for spatial transitions |
| Keep tile zoom at 200-300ms total | Use decorative flourishes or slow-motion effects |
| Always include `prefers-reduced-motion` fallback | Forget reduced motion (tokens handle it, but check @keyframes) |

### Component Patterns

| Do | Don't |
|----|-------|
| Show bottom of terminal content (most recent) with top fade | Show top of content with scrollbars |
| Use amber dot for bell — visible, non-semantic | Use red for bell (implies error) or green (implies success) |
| Use command palette (keyboard-triggered overlay) for session switching | Use persistent sidebar (steals terminal space) |
| Keep tile height fixed for spatial scanning | Use variable-height tiles (breaks scan and position memory) |

### Dark / Light Mode

| Do | Don't |
|----|-------|
| Use CSS custom properties — values swap automatically | Hardcode hex colors in component styles |
| Test both modes when adding new UI elements | Assume dark-mode-only (light mode tokens exist for a reason) |
| Use `data-theme` attribute for user override | Use class names for theme switching (attributes are cleaner) |
| Use luminance stepping for depth in dark mode | Rely on shadows for depth in dark mode (they're invisible) |

---

## File Inventory

| File | Format | Purpose |
|------|--------|---------|
| `tokens.css` | CSS custom properties | Link into HTML — single source of truth for styles |
| `tokens.json` | Structured JSON | Machine-readable for tooling, scripts, or framework config |
| `DESIGN-SYSTEM.md` | This document | Human-readable specification and usage guide |

All three files live in `muxplex/assets/branding/` alongside the existing brand
assets (icons, wordmark, lockup, etc.).
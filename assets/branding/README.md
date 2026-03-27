# muxplex Brand Assets

## Source files (SVGs)
All brand assets are derived from these source SVGs in `svg/`:

| File | Use |
|------|-----|
| `svg/icon/muxplex-icon-dark.svg` | App icon — dark background version |
| `svg/icon/muxplex-icon-light.svg` | App icon — light background version |
| `svg/wordmark/wordmark-on-dark.svg` | Wordmark text — for use on dark surfaces |
| `svg/wordmark/wordmark-on-light.svg` | Wordmark text — for use on light surfaces |
| `svg/lockup/lockup-on-dark.svg` | Icon + wordmark combined — dark |
| `svg/lockup/lockup-on-light.svg` | Icon + wordmark combined — light |

## Generated assets
Run `../scripts/render-brand-assets.py` to regenerate everything from the SVG sources.

| Directory | Contents |
|-----------|----------|
| `icons/` | App icons at 16–1024px |
| `favicons/` | favicon.ico, favicon-*.png, apple-touch-icon.png |
| `pwa/` | PWA manifest icons (192, 512px) |
| `og/` | Open Graph images 1200×630 (dark + light) |
| `wordmark/` | Wordmark PNGs at 32 and 64px height |
| `lockup/` | Lockup PNGs at 32 and 64px height |

## Wordmark note
Font: Urbanist 700 (Google Fonts). SVGs reference Google Fonts CDN.
For offline rendering, install locally: `pip install cairosvg` and ensure network access
or embed the font in the SVG beforehand.

## Colour palette
| Token | Hex | Use |
|-------|-----|-----|
| Dark background | `#0D1117` | Primary dark bg |
| Icon inner | `#10131C` | Icon card bg (dark) |
| White / light text | `#F0F6FF` | "mux" wordmark, icon frame |
| Cyan | `#00D9F5` | "plex" wordmark, cyan traffic light |
| Amber | `#F1A640` | Amber traffic light, accent |
| Light panes | `#E8E9EE` | Icon card bg (light) |

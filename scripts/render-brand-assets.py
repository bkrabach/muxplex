#!/usr/bin/env python3
"""
muxplex brand asset renderer
Usage: python3 render-brand-assets.py [--force] [--workdir PATH]

Renders all SVG sources to PNG/ICO for web, PWA, favicons, and OG images.
Run from the ./muxplex/ directory or pass --workdir path.

Renderer priority: cairosvg > rsvg-convert > inkscape > imagemagick (convert/magick)
"""

import importlib.util
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from argparse import ArgumentParser
from pathlib import Path

# ──────────────────────────────────────────────────────────────
# Argument parsing
# ──────────────────────────────────────────────────────────────

parser = ArgumentParser(description="Render muxplex brand assets")
parser.add_argument(
    "--force", action="store_true", help="Re-render even if output exists"
)
parser.add_argument("--workdir", default=None, help="Base directory (default: cwd)")
args = parser.parse_args()

WORKDIR = Path(args.workdir).resolve() if args.workdir else Path.cwd()
ASSETS = WORKDIR / "assets" / "branding"
SVG_DIR = ASSETS / "svg"
FORCE = args.force

# Verify we're in the right place
if not SVG_DIR.exists():
    sys.exit(
        f"[ERROR] Cannot find {SVG_DIR}\n"
        "Run from the muxplex/ directory or pass --workdir <path-to-muxplex-dir>"
    )

# ──────────────────────────────────────────────────────────────
# Renderer detection
# ──────────────────────────────────────────────────────────────

RENDERER: str | None = None

if importlib.util.find_spec("cairosvg") is not None:
    RENDERER = "cairosvg"
    print("[renderer] cairosvg detected ✓")

if RENDERER is None and shutil.which("rsvg-convert"):
    RENDERER = "rsvg-convert"
    print("[renderer] rsvg-convert detected ✓")

if RENDERER is None and shutil.which("inkscape"):
    RENDERER = "inkscape"
    print("[renderer] inkscape detected ✓")

if RENDERER is None:
    for _magick_bin in ("magick", "convert"):
        if shutil.which(_magick_bin):
            RENDERER = f"imagemagick:{_magick_bin}"
            print(f"[renderer] ImageMagick ({_magick_bin}) detected ✓")
            break

if RENDERER is None:
    sys.exit(
        "[ERROR] No SVG renderer found.\n"
        "Install one of: cairosvg (pip install cairosvg), librsvg2-bin (rsvg-convert),\n"
        "                inkscape, or imagemagick"
    )

# ──────────────────────────────────────────────────────────────
# Pillow detection
# ──────────────────────────────────────────────────────────────

HAS_PILLOW = importlib.util.find_spec("PIL") is not None
if HAS_PILLOW:
    print("[pillow] Pillow detected ✓")
else:
    print("[pillow] Pillow not available — ICO and OG composition will be skipped")

# ──────────────────────────────────────────────────────────────
# SVG introspection helpers
# ──────────────────────────────────────────────────────────────


def get_svg_dimensions(svg_path: Path) -> tuple[float | None, float | None]:
    """Return (width, height) floats from the SVG root element."""
    tree = ET.parse(svg_path)
    root = tree.getroot()
    w = root.get("width")
    h = root.get("height")
    # Fall back to viewBox
    if not w or not h:
        vb = root.get("viewBox", "")
        parts = vb.replace(",", " ").split()
        if len(parts) == 4:
            w, h = parts[2], parts[3]
    if w and h:
        # Strip units (px, pt, etc.)
        w_f = float("".join(c for c in str(w) if c in "0123456789."))
        h_f = float("".join(c for c in str(h) if c in "0123456789."))
        return w_f, h_f
    return None, None


# ──────────────────────────────────────────────────────────────
# Core render function
# ──────────────────────────────────────────────────────────────


def render_svg(
    src: Path,
    dst: Path,
    width: int | None = None,
    height: int | None = None,
) -> bool:
    """
    Render src SVG to dst PNG at the given width and/or height.
    If only one dimension is given the other is computed from aspect ratio.
    Returns True on success.
    """
    if dst.exists() and not FORCE:
        print(f"  [skip]  {dst.relative_to(WORKDIR)}")
        return True

    dst.parent.mkdir(parents=True, exist_ok=True)

    # Compute missing dimension from aspect ratio
    svg_w, svg_h = get_svg_dimensions(src)
    if svg_w and svg_h:
        aspect = svg_w / svg_h
        if width and not height:
            height = round(width / aspect)
        elif height and not width:
            width = round(height * aspect)
    # Final fallback dimensions
    final_w: int = width if width is not None else int(svg_w or 64)
    final_h: int = height if height is not None else int(svg_h or 64)

    try:
        if RENDERER == "cairosvg":
            import cairosvg  # type: ignore[import-untyped]

            cairosvg.svg2png(
                url=str(src),
                write_to=str(dst),
                output_width=final_w,
                output_height=final_h,
            )

        elif RENDERER == "rsvg-convert":
            subprocess.run(
                [
                    "rsvg-convert",
                    "-w",
                    str(final_w),
                    "-h",
                    str(final_h),
                    "-o",
                    str(dst),
                    str(src),
                ],
                check=True,
                capture_output=True,
            )

        elif RENDERER == "inkscape":
            subprocess.run(
                [
                    "inkscape",
                    "--export-type=png",
                    f"--export-width={final_w}",
                    f"--export-height={final_h}",
                    f"--export-filename={dst}",
                    str(src),
                ],
                check=True,
                capture_output=True,
            )

        else:
            # ImageMagick — RENDERER is "imagemagick:convert" or "imagemagick:magick"
            #
            # ImageMagick's built-in SVG renderer (MSVG) handles clip-paths
            # reliably only at the SVG's native pixel dimensions. Rendering at
            # any other size typically produces a 1-colour (transparent) result.
            #
            # Strategy:
            #   1. Render to a temp PNG at native SVG dimensions via ImageMagick.
            #   2. Scale the temp PNG to the requested target size with Pillow
            #      (LANCZOS — high quality, preserves edges).
            #   3. Fall back to a direct ImageMagick resize if Pillow is absent
            #      (quality may be poor for upscaling).
            assert RENDERER is not None  # guaranteed by startup check
            bin_name = RENDERER.split(":")[1]
            native_w, native_h = get_svg_dimensions(src)

            if native_w and native_h and HAS_PILLOW:
                from PIL import Image

                tmp_native = dst.parent / f"_tmp_native_{src.stem}.png"
                # Render at native SVG size (no resize flag → ImageMagick uses
                # the dimensions declared in the SVG header).
                subprocess.run(
                    [bin_name, "-background", "none", str(src), str(tmp_native)],
                    check=True,
                    capture_output=True,
                )
                # Pillow: open → resize with LANCZOS → save
                img = Image.open(tmp_native).convert("RGBA")
                img = img.resize((final_w, final_h), Image.Resampling.LANCZOS)
                img.save(str(dst))
                tmp_native.unlink(missing_ok=True)
            else:
                # No Pillow — attempt a direct ImageMagick resize.
                # Works acceptably for downscaling; upscaling may be imprecise.
                subprocess.run(
                    [
                        bin_name,
                        "-background",
                        "none",
                        "-resize",
                        f"{final_w}x{final_h}!",
                        str(src),
                        str(dst),
                    ],
                    check=True,
                    capture_output=True,
                )

        print(f"  [ok]    {dst.relative_to(WORKDIR)}  ({final_w}×{final_h})")
        return True

    except Exception as exc:
        print(f"  [FAIL]  {dst.relative_to(WORKDIR)} — {exc}")
        return False


# ──────────────────────────────────────────────────────────────
# Generated-file registry (for summary table)
# ──────────────────────────────────────────────────────────────

generated: list[dict[str, str]] = []


def track(path: Path, status: str = "ok") -> None:
    size_kb = f"{path.stat().st_size / 1024:.1f}K" if path.exists() else "—"
    generated.append(
        {"path": str(path.relative_to(WORKDIR)), "size": size_kb, "status": status}
    )


# ──────────────────────────────────────────────────────────────
# Step 1 — App icons
# ──────────────────────────────────────────────────────────────

print("\n── App icons ─────────────────────────────────────────────")
ICON_SRC = SVG_DIR / "icon" / "muxplex-icon-dark.svg"
ICONS_DIR = ASSETS / "icons"
ICON_SIZES = [16, 22, 24, 32, 48, 64, 128, 192, 256, 512, 1024]

for sz in ICON_SIZES:
    dst = ICONS_DIR / f"muxplex-icon-{sz}.png"
    ok = render_svg(ICON_SRC, dst, width=sz, height=sz)
    if dst.exists():
        track(dst, "ok" if ok else "fail")

# ──────────────────────────────────────────────────────────────
# Step 2 — Favicons
# ──────────────────────────────────────────────────────────────

print("\n── Favicons ──────────────────────────────────────────────")
FAV_DIR = ASSETS / "favicons"

for sz in [16, 32, 48]:
    dst = FAV_DIR / f"favicon-{sz}.png"
    ok = render_svg(ICON_SRC, dst, width=sz, height=sz)
    if dst.exists():
        track(dst)

# apple-touch-icon 180×180
ati = FAV_DIR / "apple-touch-icon.png"
ok = render_svg(ICON_SRC, ati, width=180, height=180)
if ati.exists():
    track(ati)

# favicon.ico (multi-res 16+32+48) — requires Pillow
ico_path = FAV_DIR / "favicon.ico"
if not ico_path.exists() or FORCE:
    if HAS_PILLOW:
        from PIL import Image

        fav_pngs = [FAV_DIR / f"favicon-{s}.png" for s in [16, 32, 48]]
        if all(p.exists() for p in fav_pngs):
            imgs = [Image.open(p).convert("RGBA") for p in fav_pngs]
            imgs[0].save(
                str(ico_path),
                format="ICO",
                sizes=[(s, s) for s in [16, 32, 48]],
                append_images=imgs[1:],
            )
            print(f"  [ok]    {ico_path.relative_to(WORKDIR)}  (multi-res ICO)")
        else:
            print("  [WARN]  favicon PNGs missing — ICO skipped")
    else:
        print("  [skip]  favicon.ico — Pillow not available")
if ico_path.exists():
    track(ico_path)

# ──────────────────────────────────────────────────────────────
# Step 3 — PWA icons
# ──────────────────────────────────────────────────────────────

print("\n── PWA icons ─────────────────────────────────────────────")
PWA_DIR = ASSETS / "pwa"

for sz in [192, 512]:
    dst = PWA_DIR / f"pwa-{sz}.png"
    ok = render_svg(ICON_SRC, dst, width=sz, height=sz)
    if dst.exists():
        track(dst)

# ──────────────────────────────────────────────────────────────
# Step 4 — Wordmark PNGs
# ──────────────────────────────────────────────────────────────

print("\n── Wordmarks ─────────────────────────────────────────────")
WM_DIR = ASSETS / "wordmark"

for variant in ["dark", "light"]:
    src = SVG_DIR / "wordmark" / f"wordmark-on-{variant}.svg"
    for h in [32, 64]:
        dst = WM_DIR / f"wordmark-on-{variant}-{h}.png"
        ok = render_svg(src, dst, height=h)
        if dst.exists():
            track(dst)

# ──────────────────────────────────────────────────────────────
# Step 5 — Lockup PNGs
# ──────────────────────────────────────────────────────────────

print("\n── Lockups ───────────────────────────────────────────────")
LOCKUP_DIR = ASSETS / "lockup"

for variant in ["dark", "light"]:
    src = SVG_DIR / "lockup" / f"lockup-on-{variant}.svg"
    for h in [32, 64]:
        dst = LOCKUP_DIR / f"lockup-on-{variant}-{h}.png"
        ok = render_svg(src, dst, height=h)
        if dst.exists():
            track(dst)

# ──────────────────────────────────────────────────────────────
# Step 6 — OG images (1200×630)
# ──────────────────────────────────────────────────────────────

print("\n── OG images ─────────────────────────────────────────────")
OG_DIR = ASSETS / "og"
OG_DIR.mkdir(parents=True, exist_ok=True)

OG_CONFIGS: list[dict] = [
    {
        "variant": "dark",
        "bg_hex": "#0D1117",
        "bg_rgb": (13, 17, 23),
        "lockup_src": SVG_DIR / "lockup" / "lockup-on-dark.svg",
        "out": OG_DIR / "og-dark.png",
    },
    {
        "variant": "light",
        "bg_hex": "#FFFFFF",
        "bg_rgb": (255, 255, 255),
        "lockup_src": SVG_DIR / "lockup" / "lockup-on-light.svg",
        "out": OG_DIR / "og-light.png",
    },
]

for cfg in OG_CONFIGS:
    out_path: Path = cfg["out"]
    if out_path.exists() and not FORCE:
        print(f"  [skip]  {out_path.relative_to(WORKDIR)}")
        track(out_path, "skip")
        continue

    if HAS_PILLOW:
        from PIL import Image

        # Render lockup at height=200 into a temp file
        tmp_lockup = OG_DIR / f"_tmp_lockup_{cfg['variant']}.png"
        ok = render_svg(cfg["lockup_src"], tmp_lockup, height=200)
        if not ok or not tmp_lockup.exists():
            print(f"  [FAIL]  OG {cfg['variant']} — lockup render failed")
            continue

        bg = Image.new("RGB", (1200, 630), color=cfg["bg_rgb"])
        lockup_img = Image.open(tmp_lockup).convert("RGBA")

        # Centre the lockup on the canvas
        x = (1200 - lockup_img.width) // 2
        y = (630 - lockup_img.height) // 2
        bg.paste(lockup_img, (x, y), lockup_img)
        bg.save(str(out_path))
        tmp_lockup.unlink(missing_ok=True)
        print(
            f"  [ok]    {out_path.relative_to(WORKDIR)}  (1200×630, bg={cfg['bg_hex']})"
        )

    else:
        # Fallback: render lockup at height=200, no canvas composition
        ok = render_svg(cfg["lockup_src"], out_path, height=200)
        if ok:
            print(
                f"  [NOTE]  {out_path.relative_to(WORKDIR)} — "
                "Pillow unavailable; rendered lockup only (no 1200×630 canvas).\n"
                "         Install Pillow and re-run to get proper OG images."
            )

    if out_path.exists():
        track(out_path)

# ──────────────────────────────────────────────────────────────
# Summary table
# ──────────────────────────────────────────────────────────────

print("\n" + "═" * 70)
print(f"  {'FILE':<52}  {'SIZE':>6}  STATUS")
print("─" * 70)
for item in generated:
    status_icon = (
        "✓" if item["status"] == "ok" else ("·" if item["status"] == "skip" else "✗")
    )
    print(f"  {item['path']:<52}  {item['size']:>6}  {status_icon}")
print("═" * 70)
print(f"  Total: {len(generated)} files\n")

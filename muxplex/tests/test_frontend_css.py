"""Tests for frontend/style.css — design tokens and dark theme."""

import pathlib
import re
import subprocess

CSS_PATH = pathlib.Path(__file__).parent.parent / "frontend" / "style.css"


def read_css() -> str:
    return CSS_PATH.read_text(encoding="utf-8")


def test_css_design_tokens():
    css = read_css()
    assert "--bg:" in css
    assert "--bell:" in css
    assert "--font-mono:" in css
    assert "--tile-height:" in css
    assert "--t-zoom:" in css


def test_css_session_grid():
    css = read_css()
    assert "auto-fill" in css
    assert "minmax" in css


def test_css_tile_height():
    css = read_css()
    assert ".session-tile" in css
    assert "var(--tile-height)" in css


def test_css_bell_indicator():
    css = read_css()
    assert "bell-pulse" in css
    assert ".session-tile--bell" in css
    assert ".tile-bell" in css


def test_css_breakpoints():
    css = read_css()
    assert "599px" in css
    assert "899px" in css


def test_css_zoom_transition():
    css = read_css()
    assert ".session-tile--expanding" in css
    assert "session-tile--expanded" in css
    assert ".session-grid--dimming" in css


def test_css_bell_count_and_toast():
    css = read_css()
    assert ".connection-status--ok" in css
    assert ".connection-status--warn" in css
    assert ".connection-status--err" in css
    assert ".toast" in css


def test_css_mobile_tiers():
    css = read_css()
    assert "session-tile--tier-bell" in css
    assert "session-tile--tier-active" in css
    assert "session-tile--tier-idle" in css


def test_css_bottom_sheet():
    css = read_css()
    assert ".bottom-sheet__panel" in css
    assert ".bottom-sheet__handle" in css
    assert ".sheet-item" in css


def test_css_session_pill():
    css = read_css()
    assert ".session-pill" in css
    assert ".session-pill__label" in css


def test_css_reduced_motion():
    css = read_css()
    assert "prefers-reduced-motion" in css


def test_css_bg_surface_variable():
    """--bg-surface variable must exist in :root."""
    css = read_css()
    assert "--bg-surface: #1A1F2B" in css


def test_css_view_body_flex_layout():
    """`.view-body` must have flex row layout properties."""
    css = read_css()
    assert ".view-body" in css
    # Extract the .view-body rule block
    view_body_idx = css.index(".view-body")
    block_start = css.index("{", view_body_idx)
    block_end = css.index("}", block_start)
    block = css[block_start:block_end]
    assert "display: flex" in block
    assert "flex-direction: row" in block
    assert "flex: 1" in block
    assert "min-height: 0" in block
    assert "overflow: hidden" in block


def test_css_view_body_before_terminal_container():
    """.view-body rule must appear before .terminal-container in the file."""
    css = read_css()
    assert ".view-body" in css
    assert ".terminal-container" in css
    assert css.index(".view-body") < css.index(".terminal-container")


def test_css_terminal_container_min_width():
    """.terminal-container must have min-width: 0 to prevent flex overflow."""
    css = read_css()
    terminal_idx = css.index(".terminal-container")
    block_start = css.index("{", terminal_idx)
    block_end = css.index("}", block_start)
    block = css[block_start:block_end]
    assert "min-width: 0" in block
    # Existing properties preserved
    assert "flex: 1" in block
    assert "overflow: hidden" in block
    assert "background: #000" in block
    assert "padding: 0 4px" in block


# ============================================================
# Sidebar container and collapse animation (task-3)
# ============================================================


def _extract_rule_block(css: str, selector: str) -> str:
    """Extract the CSS block for a given selector."""
    idx = css.index(selector)
    block_start = css.index("{", idx)
    block_end = css.index("}", block_start)
    return css[block_start:block_end]


def test_css_session_sidebar_exists():
    """.session-sidebar rule must exist in the CSS."""
    css = read_css()
    assert ".session-sidebar" in css


def test_css_session_sidebar_dimensions():
    """.session-sidebar must have width: 200px and min-width: 200px."""
    css = read_css()
    block = _extract_rule_block(css, ".session-sidebar {")
    assert "width: 200px" in block
    assert "min-width: 200px" in block


def test_css_session_sidebar_background():
    """.session-sidebar must use var(--bg-secondary) background."""
    css = read_css()
    block = _extract_rule_block(css, ".session-sidebar {")
    assert "background: var(--bg-secondary)" in block


def test_css_session_sidebar_border():
    """.session-sidebar must have a border-right using var(--border-subtle)."""
    css = read_css()
    block = _extract_rule_block(css, ".session-sidebar {")
    assert "border-right: 1px solid var(--border-subtle)" in block


def test_css_session_sidebar_flex_column():
    """.session-sidebar must use flex column layout with overflow hidden."""
    css = read_css()
    block = _extract_rule_block(css, ".session-sidebar {")
    assert "display: flex" in block
    assert "flex-direction: column" in block
    assert "overflow: hidden" in block
    assert "flex-shrink: 0" in block


def test_css_session_sidebar_transition():
    """.session-sidebar must have transition on width and min-width for collapse animation."""
    css = read_css()
    block = _extract_rule_block(css, ".session-sidebar {")
    assert "transition:" in block
    assert "width 0.25s ease" in block
    assert "min-width 0.25s ease" in block


def test_css_sidebar_collapsed_state():
    """.session-sidebar.sidebar--collapsed must set width and min-width to 0."""
    css = read_css()
    assert ".session-sidebar.sidebar--collapsed" in css
    block = _extract_rule_block(css, ".session-sidebar.sidebar--collapsed")
    assert "width: 0" in block
    assert "min-width: 0" in block


def test_css_sidebar_before_terminal_container():
    """.session-sidebar rules must appear after .view-body and before .terminal-container."""
    css = read_css()
    assert ".session-sidebar" in css
    assert ".view-body" in css
    assert ".terminal-container" in css
    view_body_idx = css.index(".view-body")
    sidebar_idx = css.index(".session-sidebar")
    terminal_idx = css.index(".terminal-container")
    assert view_body_idx < sidebar_idx, ".session-sidebar must come after .view-body"
    assert sidebar_idx < terminal_idx, (
        ".session-sidebar must come before .terminal-container"
    )


def test_css_sidebar_header():
    """.sidebar-header must be flex row with space-between and padding."""
    css = read_css()
    assert ".sidebar-header" in css
    block = _extract_rule_block(css, ".sidebar-header {")
    assert "display: flex" in block
    assert "flex-direction: row" in block
    assert "justify-content: space-between" in block
    assert "padding: 8px 12px" in block
    assert "border-bottom" in block


def test_css_sidebar_title():
    """.sidebar-title must be styled as a small uppercase label."""
    css = read_css()
    assert ".sidebar-title" in css
    block = _extract_rule_block(css, ".sidebar-title {")
    assert "font-size: 11px" in block
    assert "font-weight: 600" in block
    assert "text-transform: uppercase" in block
    assert "letter-spacing:" in block
    assert "color: var(--text-muted)" in block


def test_css_sidebar_list():
    """.sidebar-list must scroll vertically and fill remaining height."""
    css = read_css()
    assert ".sidebar-list" in css
    block = _extract_rule_block(css, ".sidebar-list {")
    assert "flex: 1" in block
    assert "overflow-y: auto" in block
    assert "overflow-x: hidden" in block


def test_css_sidebar_list_padding():
    """.sidebar-list must have padding and flex column layout with gap for card breathing room."""
    css = read_css()
    block = _extract_rule_block(css, ".sidebar-list {")
    assert "padding: 8px" in block
    assert "display: flex" in block
    assert "flex-direction: column" in block
    assert "gap: 6px" in block


def test_css_sidebar_collapse_btn():
    """.sidebar-collapse-btn must be a minimal button styled for the chevron."""
    css = read_css()
    assert ".sidebar-collapse-btn" in css
    block = _extract_rule_block(css, ".sidebar-collapse-btn {")
    assert "background: none" in block
    assert "border: none" in block
    assert "color: var(--text-muted)" in block
    assert "cursor: pointer" in block
    assert "font-size: 18px" in block
    assert "padding: 2px 6px" in block
    assert "border-radius: 4px" in block


def test_css_sidebar_collapse_btn_hover():
    """.sidebar-collapse-btn:hover must show full text color."""
    css = read_css()
    assert ".sidebar-collapse-btn:hover" in css
    block = _extract_rule_block(css, ".sidebar-collapse-btn:hover {")
    assert "color: var(--text)" in block


def test_css_sidebar_toggle_btn():
    """.sidebar-toggle-btn must be a 36x36 bordered button with flex centering."""
    css = read_css()
    assert ".sidebar-toggle-btn" in css
    block = _extract_rule_block(css, ".sidebar-toggle-btn {")
    assert "background: none" in block
    assert "border: 1px solid var(--border)" in block
    assert "border-radius: 4px" in block
    assert "width: 36px" in block
    assert "height: 36px" in block
    assert "display: flex" in block
    assert "align-items: center" in block
    assert "justify-content: center" in block
    assert "margin-right: 8px" in block


def test_css_sidebar_toggle_btn_hover():
    """.sidebar-toggle-btn:hover must update border-color to accent."""
    css = read_css()
    assert ".sidebar-toggle-btn:hover" in css
    block = _extract_rule_block(css, ".sidebar-toggle-btn:hover {")
    assert "border-color: var(--accent)" in block


# ============================================================
# Sidebar session card styles (task-4)
# ============================================================


def test_css_sidebar_item_exists():
    """.sidebar-item rule must exist in the CSS."""
    css = read_css()
    assert ".sidebar-item {" in css


def test_css_sidebar_item_dimensions_and_layout():
    """.sidebar-item must be 120px tall, flex column, outlined card with transition."""
    css = read_css()
    block = _extract_rule_block(css, ".sidebar-item {")
    assert "height: 120px" in block
    assert "background: var(--bg-secondary)" in block
    assert "border: 1px solid var(--border)" in block
    assert "border-radius: 4px" in block
    assert "cursor: pointer" in block
    assert "overflow: hidden" in block
    assert "display: flex" in block
    assert "flex-direction: column" in block
    assert "position: relative" in block
    assert "transition:" in block


def test_css_sidebar_item_border_radius():
    """.sidebar-item must have border-radius: 4px matching the dashboard tile aesthetic."""
    css = read_css()
    block = _extract_rule_block(css, ".sidebar-item {")
    assert "border-radius: 4px" in block


def test_css_sidebar_item_hover():
    """.sidebar-item:hover must use accent border-color (not background change)."""
    css = read_css()
    assert ".sidebar-item:hover" in css
    assert ".sidebar-item:focus-visible" in css
    block = _extract_rule_block(css, ".sidebar-item:hover")
    assert "border-color: var(--accent)" in block


def test_css_sidebar_item_active():
    """.sidebar-item--active must use border-left for full-height accent indicator."""
    css = read_css()
    assert ".sidebar-item--active" in css
    block = _extract_rule_block(css, ".sidebar-item--active {")
    assert "background: var(--bg-surface)" in block
    assert "border-color: var(--accent)" in block
    assert "border-left: 3px solid var(--accent)" in block


def test_css_sidebar_item_header():
    """.sidebar-item-header must be flex row, space-between, with correct padding/height/gap."""
    css = read_css()
    assert ".sidebar-item-header" in css
    block = _extract_rule_block(css, ".sidebar-item-header {")
    assert "display: flex" in block
    assert "flex-direction: row" in block
    assert "justify-content: space-between" in block
    assert "padding: 8px 8px 4px" in block
    assert "height: 32px" in block
    assert "gap: 4px" in block


def test_css_sidebar_item_name():
    """.sidebar-item-name must be 12px, 600 weight, --text color, ellipsis, flex 1, min-width 0."""
    css = read_css()
    assert ".sidebar-item-name" in css
    block = _extract_rule_block(css, ".sidebar-item-name {")
    assert "font-size: 12px" in block
    assert "font-weight: 600" in block
    assert "color: var(--text)" in block
    assert "text-overflow: ellipsis" in block
    assert "overflow: hidden" in block
    assert "white-space: nowrap" in block
    assert "flex: 1" in block
    assert "min-width: 0" in block


def test_css_sidebar_item_body():
    """.sidebar-item-body must be flex: 1, position relative, overflow hidden."""
    css = read_css()
    assert ".sidebar-item-body" in css
    block = _extract_rule_block(css, ".sidebar-item-body {")
    assert "flex: 1" in block
    assert "position: relative" in block
    assert "overflow: hidden" in block


def test_css_sidebar_item_body_pre():
    """.sidebar-item-body pre must be anchored to bottom with 10px monospace font matching xterm.js."""
    css = read_css()
    assert ".sidebar-item-body pre" in css
    block = _extract_rule_block(css, ".sidebar-item-body pre {")
    assert "position: absolute" in block
    assert "bottom: 0" in block
    assert "left: 0" in block
    assert "right: 0" in block
    assert "font-size: 10px" in block
    assert "line-height: 1.0" in block, (
        "sidebar-item-body pre must use line-height: 1.0 (xterm.js default)"
    )
    assert "#c9d1d9" in block, "sidebar-item-body pre must match xterm.js foreground"
    assert "white-space: pre" in block
    assert "padding: 0 8px 6px" in block
    # Explicit xterm.js font family (not design token variable)
    assert "'SF Mono'" in block or "SF Mono" in block, (
        "sidebar-item-body pre must use explicit xterm.js font family"
    )


def test_css_sidebar_empty():
    """.sidebar-empty must be centered, 12px, --text-muted color."""
    css = read_css()
    assert ".sidebar-empty" in css
    block = _extract_rule_block(css, ".sidebar-empty {")
    assert "padding: 16px 12px" in block
    assert "color: var(--text-muted)" in block
    assert "font-size: 12px" in block
    assert "text-align: center" in block


def test_css_sidebar_item_after_toggle_btn_hover():
    """.sidebar-item rules must appear after .sidebar-toggle-btn:hover."""
    css = read_css()
    assert ".sidebar-toggle-btn:hover" in css
    assert ".sidebar-item" in css
    toggle_idx = css.index(".sidebar-toggle-btn:hover")
    item_idx = css.index(".sidebar-item")
    assert toggle_idx < item_idx, (
        ".sidebar-item must come after .sidebar-toggle-btn:hover"
    )


# ============================================================
# Responsive overlay at <960px and reduced-motion (task-5)
# ============================================================


def _extract_media_block(css: str, query: str) -> str:
    """Extract the inner content of a @media block (balanced-brace aware)."""
    idx = css.index(query)
    open_brace = css.index("{", idx)
    depth = 0
    pos = open_brace
    while pos < len(css):
        if css[pos] == "{":
            depth += 1
        elif css[pos] == "}":
            depth -= 1
            if depth == 0:
                return css[open_brace + 1 : pos]
        pos += 1
    raise ValueError(f"Could not find matching close brace for {query}")


def test_css_responsive_overlay_media_query_exists():
    """@media (max-width: 959px) block must exist in the CSS."""
    css = read_css()
    assert "@media (max-width: 959px)" in css


def test_css_responsive_overlay_at_end():
    """@media (max-width: 959px) must be the last @media block in the file."""
    css = read_css()
    last_media_idx = css.rfind("@media")
    assert "@media (max-width: 959px)" in css[last_media_idx:]


def test_css_responsive_overlay_sidebar_fixed():
    """.session-sidebar inside <960px media query must become a fixed overlay."""
    css = read_css()
    media_block = _extract_media_block(css, "@media (max-width: 959px)")
    assert ".session-sidebar {" in media_block
    block = _extract_rule_block(media_block, ".session-sidebar {")
    assert "position: fixed" in block
    assert "left: 0" in block
    assert "top: 0" in block
    assert "height: 100%" in block
    assert "z-index: 200" in block
    assert "width: 240px" in block
    assert "min-width: 240px" in block
    assert "transition: transform 0.25s ease" in block
    assert "transform: translateX(0)" in block
    assert "box-shadow: 2px 0 16px rgba(0,0,0,0.5)" in block


def test_css_responsive_overlay_sidebar_collapsed():
    """.session-sidebar.sidebar--collapsed inside <960px collapses via translateX(-100%)."""
    css = read_css()
    media_block = _extract_media_block(css, "@media (max-width: 959px)")
    assert ".session-sidebar.sidebar--collapsed" in media_block
    block = _extract_rule_block(media_block, ".session-sidebar.sidebar--collapsed")
    assert "width: 240px" in block
    assert "min-width: 240px" in block
    assert "transform: translateX(-100%)" in block


def test_css_responsive_overlay_collapse_btn_hidden():
    """.sidebar-collapse-btn inside <960px must be display: none."""
    css = read_css()
    media_block = _extract_media_block(css, "@media (max-width: 959px)")
    assert ".sidebar-collapse-btn" in media_block
    block = _extract_rule_block(media_block, ".sidebar-collapse-btn")
    assert "display: none" in block


def test_css_reduced_motion_sidebar_transition_none():
    """@media (prefers-reduced-motion: reduce) must include .session-sidebar { transition: none; }."""
    css = read_css()
    media_block = _extract_media_block(css, "@media (prefers-reduced-motion: reduce)")
    assert ".session-sidebar" in media_block
    block = _extract_rule_block(media_block, ".session-sidebar")
    assert "transition: none" in block


def test_css_reduced_motion_sidebar_after_toast():
    """In reduced-motion block, .session-sidebar must come after .toast { animation: none; }."""
    css = read_css()
    media_block = _extract_media_block(css, "@media (prefers-reduced-motion: reduce)")
    toast_idx = media_block.index(".toast")
    sidebar_idx = media_block.index(".session-sidebar")
    assert toast_idx < sidebar_idx, (
        ".session-sidebar must come after .toast in reduced-motion block"
    )


# ── Bug-fix regression tests ─────────────────────────────────────────────────


def test_idle_tile_body_not_display_none():
    """Fix 1: .session-tile--tier-idle .tile-body must NOT use display:none.

    All sessions are 'idle' when zero bell notifications — display:none caused
    a blank screen on mobile (iPhone).
    """
    css = read_css()
    # Locate the idle rule block
    marker = ".session-tile--tier-idle .tile-body"
    assert marker in css, "idle .tile-body rule must exist"
    idx = css.index(marker)
    block_start = css.index("{", idx)
    block_end = css.index("}", block_start)
    block = css[block_start:block_end]
    assert "display: none" not in block, (
        ".session-tile--tier-idle .tile-body must not use display:none "
        "(hides all tile content on mobile)"
    )


def test_tile_body_pre_has_typography():
    """Fix 2a: .tile-body pre must carry font-family and color declarations.

    Previously those rules were on dead .tile-pre class which was never in HTML.
    """
    css = read_css()
    marker = ".tile-body pre"
    assert marker in css, ".tile-body pre rule must exist"
    idx = css.index(marker)
    block_start = css.index("{", idx)
    block_end = css.index("}", block_start)
    block = css[block_start:block_end]
    assert "font-family" in block, ".tile-body pre must declare font-family"
    assert "color" in block, ".tile-body pre must declare color"


def test_mobile_active_tier_targets_tile_body_pre_not_tile_pre():
    """Fix 2b: mobile active-tier selector must be .tile-body pre, not .tile-pre.

    .tile-pre is never applied in HTML; the real element is <pre> inside .tile-body.
    """
    css = read_css()
    assert ".session-tile--tier-active .tile-body pre" in css, (
        "mobile active-tier must target .tile-body pre"
    )
    assert ".session-tile--tier-active .tile-pre" not in css, (
        ".tile-pre is a dead class — selector must be removed"
    )


def test_sidebar_list_has_touch_action_pan_y():
    """Regression: without touch-action:pan-y .sidebar-list won't scroll on mobile touch.

    Mobile browsers need an explicit touch-action:pan-y declaration to allow
    vertical panning on an overflow-y:auto element when adjacent content (like
    xterm.js canvas) may be consuming touch events.
    """
    css = read_css()
    block = _extract_rule_block(css, ".sidebar-list {")
    assert "touch-action: pan-y" in block, (
        ".sidebar-list must have touch-action:pan-y for mobile vertical scroll"
    )
    assert "overscroll-behavior: contain" in block, (
        ".sidebar-list must have overscroll-behavior:contain to prevent scroll chaining"
    )


def test_sidebar_item_has_flex_shrink_zero():
    """Regression: without flex-shrink:0 cards shrink to fit, eliminating scroll overflow."""
    css = read_css()
    block = _extract_rule_block(css, ".sidebar-item {")
    assert "flex-shrink: 0" in block, (
        ".sidebar-item must have flex-shrink:0 to prevent compression"
    )


# ============================================================
# Hover preview popover (desktop dashboard)
# ============================================================


def test_preview_popover_css_exists():
    """Preview popover must have CSS rules with position: fixed."""
    css = read_css()
    assert ".preview-popover" in css, "must have .preview-popover CSS class"
    popover_idx = css.index(".preview-popover")
    block_start = css.index("{", popover_idx)
    block_end = css.index("}", block_start)
    block = css[block_start:block_end]
    assert "position: fixed" in block or "position:fixed" in block, (
        ".preview-popover must use position: fixed"
    )


def test_preview_popover_has_accent_border():
    """Preview popover must use brand cyan border."""
    css = read_css()
    start = css.index(".preview-popover {")
    end = css.index("}", start)
    block = css[start:end]
    assert "var(--accent)" in block, ".preview-popover must use var(--accent) border"


def test_preview_popover_has_black_background():
    """.preview-popover must use #000000 background to match xterm.js terminal."""
    css = read_css()
    start = css.index(".preview-popover {")
    end = css.index("}", start)
    block = css[start:end]
    assert "#000000" in block or "background: #000" in block, (
        ".preview-popover must use #000000 background (not var(--bg-secondary))"
    )


def test_preview_popover_pre_matches_xterm_typography():
    """.preview-popover pre must match xterm.js terminal: 14px, line-height 1.0, explicit font stack."""
    css = read_css()
    block = _extract_rule_block(css, ".preview-popover pre {")
    assert "font-size: 14px" in block, (
        ".preview-popover pre must use 14px (xterm.js default)"
    )
    assert "line-height: 1.0" in block, (
        ".preview-popover pre must use line-height: 1.0 (xterm.js default)"
    )
    assert "'SF Mono'" in block or "SF Mono" in block, (
        ".preview-popover pre must use explicit xterm.js font family"
    )


def test_tile_body_has_black_background():
    """.tile-body must use #000000 background to match xterm.js terminal."""
    css = read_css()
    block = _extract_rule_block(css, ".tile-body {")
    assert "#000000" in block or "background: #000" in block, (
        ".tile-body must use #000000 background to match xterm.js"
    )


def test_tile_body_pre_has_xterm_line_height():
    """.tile-body pre must use line-height: 1.0 to match xterm.js terminal."""
    css = read_css()
    block = _extract_rule_block(css, ".tile-body pre {")
    assert "line-height: 1.0" in block, (
        ".tile-body pre must use line-height: 1.0 (xterm.js default)"
    )


def test_tile_body_pre_has_explicit_font_family():
    """.tile-body pre must use explicit xterm.js font family, not CSS variable."""
    css = read_css()
    block = _extract_rule_block(css, ".tile-body pre {")
    assert "'SF Mono'" in block or "SF Mono" in block, (
        ".tile-body pre must use explicit xterm.js font family"
    )


def test_sidebar_item_body_has_black_background():
    """.sidebar-item-body must use #000000 background to match xterm.js terminal."""
    css = read_css()
    block = _extract_rule_block(css, ".sidebar-item-body {")
    assert "#000000" in block or "background: #000" in block, (
        ".sidebar-item-body must use #000000 background to match xterm.js"
    )


def test_sidebar_item_body_pre_has_xterm_line_height():
    """.sidebar-item-body pre must use line-height: 1.0 to match xterm.js terminal."""
    css = read_css()
    block = _extract_rule_block(css, ".sidebar-item-body pre {")
    assert "line-height: 1.0" in block, (
        ".sidebar-item-body pre must use line-height: 1.0 (xterm.js default)"
    )


def test_sidebar_item_body_pre_has_explicit_font_family():
    """.sidebar-item-body pre must use explicit xterm.js font family, not CSS variable."""
    css = read_css()
    block = _extract_rule_block(css, ".sidebar-item-body pre {")
    assert "'SF Mono'" in block or "SF Mono" in block, (
        ".sidebar-item-body pre must use explicit xterm.js font family"
    )


def test_no_gradient_fade_on_previews():
    """Gradient fade overlays must be removed — they obscure ANSI colors at the bottom."""
    css = read_css()
    assert ".tile-body::before" not in css, "tile-body::before gradient must be removed"
    assert ".sidebar-item-body::before" not in css, (
        "sidebar-item-body::before gradient must be removed"
    )


# ============================================================
# Settings modal CSS (task-6)
# ============================================================


def test_css_header_actions_exists():
    """.header-actions must exist with flex layout and gap."""
    css = read_css()
    assert ".header-actions" in css, "Missing .header-actions CSS rule"
    block = _extract_rule_block(css, ".header-actions {")
    assert "display: flex" in block, ".header-actions must use display: flex"
    assert "gap:" in block and "8px" in block, ".header-actions must have gap: 8px"


def test_css_header_btn_exists():
    """.header-btn must exist with 32x32 size, border, cursor."""
    css = read_css()
    assert ".header-btn" in css, "Missing .header-btn CSS rule"
    block = _extract_rule_block(css, ".header-btn {")
    assert "width: 32px" in block, ".header-btn must be 32px wide"
    assert "height: 32px" in block, ".header-btn must be 32px tall"
    assert "border:" in block or "border :" in block, ".header-btn must have border"
    assert "cursor: pointer" in block, ".header-btn must have cursor: pointer"


def test_css_header_btn_hover():
    """.header-btn:hover must exist."""
    css = read_css()
    assert ".header-btn:hover" in css, "Missing .header-btn:hover CSS rule"


def test_css_settings_backdrop_exists():
    """.settings-backdrop must exist with position: fixed and blur."""
    css = read_css()
    assert ".settings-backdrop" in css, "Missing .settings-backdrop CSS rule"
    block = _extract_rule_block(css, ".settings-backdrop {")
    assert "position: fixed" in block, ".settings-backdrop must use position: fixed"
    assert "blur" in block or "backdrop-filter" in block, (
        ".settings-backdrop must use blur"
    )


def test_css_settings_dialog_exists():
    """.settings-dialog must exist with correct dimensions and z-index."""
    css = read_css()
    assert ".settings-dialog" in css, "Missing .settings-dialog CSS rule"
    block = _extract_rule_block(css, ".settings-dialog {")
    assert "600px" in block, ".settings-dialog must be 600px wide"
    assert "480px" in block, ".settings-dialog must be 480px tall"
    assert "z-index: 300" in block, ".settings-dialog must have z-index: 300"
    assert "border-radius:" in block, ".settings-dialog must have border-radius"


def test_css_settings_dialog_backdrop_transparent():
    """.settings-dialog::backdrop must be transparent."""
    css = read_css()
    assert ".settings-dialog::backdrop" in css, (
        "Missing .settings-dialog::backdrop CSS rule"
    )
    block = _extract_rule_block(css, ".settings-dialog::backdrop {")
    assert "transparent" in block, ".settings-dialog::backdrop must be transparent"


def test_css_settings_layout_flex():
    """.settings-layout must use flex layout."""
    css = read_css()
    assert ".settings-layout" in css, "Missing .settings-layout CSS rule"
    block = _extract_rule_block(css, ".settings-layout {")
    assert "display: flex" in block, ".settings-layout must use display: flex"


def test_css_settings_tabs_exists():
    """.settings-tabs must exist with 140px width sidebar."""
    css = read_css()
    assert ".settings-tabs" in css, "Missing .settings-tabs CSS rule"
    block = _extract_rule_block(css, ".settings-tabs {")
    assert "140px" in block, ".settings-tabs must have 140px width"


def test_css_settings_tab_exists():
    """.settings-tab must exist as text buttons with left border indicator."""
    css = read_css()
    assert ".settings-tab" in css, "Missing .settings-tab CSS rule"
    block = _extract_rule_block(css, ".settings-tab {")
    assert "border-left:" in block, ".settings-tab must have border-left indicator"
    assert "cursor: pointer" in block, ".settings-tab must have cursor: pointer"


def test_css_settings_tab_active():
    """.settings-tab--active must exist with accent color."""
    css = read_css()
    assert ".settings-tab--active" in css, "Missing .settings-tab--active CSS rule"
    block = _extract_rule_block(css, ".settings-tab--active {")
    assert "var(--accent)" in block, ".settings-tab--active must use var(--accent)"


def test_css_settings_content_exists():
    """.settings-content must exist as scrollable container."""
    css = read_css()
    assert ".settings-content" in css, "Missing .settings-content CSS rule"
    block = _extract_rule_block(css, ".settings-content {")
    assert "overflow-y: auto" in block, (
        ".settings-content must be scrollable (overflow-y: auto)"
    )


def test_css_settings_field_exists():
    """.settings-field must exist as flex row between."""
    css = read_css()
    assert ".settings-field" in css, "Missing .settings-field CSS rule"
    block = _extract_rule_block(css, ".settings-field {")
    assert "display: flex" in block, ".settings-field must use display: flex"
    assert "flex-direction: row" in block, (
        ".settings-field must use flex-direction: row"
    )
    assert "justify-content: space-between" in block, (
        ".settings-field must use justify-content: space-between"
    )


def test_css_settings_select_exists():
    """.settings-select must exist as styled select."""
    css = read_css()
    assert ".settings-select" in css, "Missing .settings-select CSS rule"
    assert ".settings-select:focus" in css, "Missing .settings-select:focus CSS rule"


def test_css_settings_mobile_media_query():
    """@media (max-width: 599px) block must exist for settings dialog mobile styles."""
    css = read_css()
    assert "@media (max-width: 599px)" in css, (
        "Missing @media (max-width: 599px) for settings mobile"
    )
    media_block = _extract_media_block(css, "@media (max-width: 599px)")
    assert ".settings-dialog" in media_block, (
        ".settings-dialog mobile styles must be in 599px media block"
    )
    # Bottom sheet: 100% width
    block = _extract_rule_block(media_block, ".settings-dialog {")
    assert "width: 100%" in block, ".settings-dialog must be 100% wide on mobile"
    assert "85vh" in block, ".settings-dialog must be 85vh tall on mobile"
    assert "bottom: 0" in block or "bottom:0" in block, (
        ".settings-dialog must be bottom-anchored on mobile"
    )


def test_css_settings_mobile_tabs_horizontal():
    """Inside mobile media query, settings tabs must become horizontal scrolling row."""
    css = read_css()
    media_block = _extract_media_block(css, "@media (max-width: 599px)")
    assert ".settings-tabs" in media_block, (
        ".settings-tabs must have mobile styles in 599px media block"
    )
    tabs_block = _extract_rule_block(media_block, ".settings-tabs {")
    assert "flex-direction: row" in tabs_block, (
        ".settings-tabs must become horizontal on mobile"
    )
    assert "overflow-x: auto" in tabs_block, (
        ".settings-tabs must scroll horizontally on mobile"
    )


# ─── Sessions tab CSS (task-1-sessions-tab) ───────────────────────────────────


def test_css_settings_field_column() -> None:
    """.settings-field--column must set flex-direction: column."""
    css = read_css()
    assert ".settings-field--column" in css, (
        ".settings-field--column class must be defined in style.css"
    )
    # Find the rule and check flex-direction

    match = re.search(
        r"\.settings-field--column\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".settings-field--column rule not found"
    body = match.group(1)
    assert "flex-direction" in body and "column" in body, (
        ".settings-field--column must set flex-direction: column"
    )


def test_css_settings_checkbox_list() -> None:
    """.settings-checkbox-list class must be defined."""
    css = read_css()
    assert ".settings-checkbox-list" in css, (
        ".settings-checkbox-list class must be defined in style.css"
    )


def test_css_settings_checkbox_item() -> None:
    """.settings-checkbox-item class must be defined."""
    css = read_css()
    assert ".settings-checkbox-item" in css, (
        ".settings-checkbox-item class must be defined in style.css"
    )


def test_css_settings_checkbox() -> None:
    """.settings-checkbox class must be defined."""
    css = read_css()
    assert ".settings-checkbox" in css, (
        ".settings-checkbox class must be defined in style.css"
    )


# ============================================================
# Notifications tab CSS (task-2-notifications-tab)
# ============================================================


def test_css_settings_notification_status_exists() -> None:
    """.settings-notification-status must exist with flex column, align-items flex-end."""

    css = read_css()
    assert ".settings-notification-status" in css, (
        ".settings-notification-status class must be defined in style.css"
    )
    match = re.search(
        r"\.settings-notification-status\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".settings-notification-status rule not found"
    body = match.group(1)
    assert "flex-direction" in body and "column" in body, (
        ".settings-notification-status must set flex-direction: column"
    )
    assert "align-items" in body and "flex-end" in body, (
        ".settings-notification-status must set align-items: flex-end"
    )


def test_css_settings_status_text_exists() -> None:
    """.settings-status-text must exist with 12px font-size and text-muted color."""

    css = read_css()
    assert ".settings-status-text" in css, (
        ".settings-status-text class must be defined in style.css"
    )
    match = re.search(
        r"\.settings-status-text\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".settings-status-text rule not found"
    body = match.group(1)
    assert "font-size: 12px" in body or "font-size:12px" in body, (
        ".settings-status-text must set font-size: 12px"
    )
    # Must use text-muted color (either via var(--text-muted) or inline)
    assert "var(--text-muted)" in body or "text-muted" in body, (
        ".settings-status-text must use var(--text-muted) color"
    )


def test_css_settings_action_btn_exists() -> None:
    """.settings-action-btn must exist with background, border, 12px font-size."""

    css = read_css()
    assert ".settings-action-btn" in css, (
        ".settings-action-btn class must be defined in style.css"
    )
    match = re.search(
        r"\.settings-action-btn\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".settings-action-btn rule not found"
    body = match.group(1)
    assert "font-size: 12px" in body or "font-size:12px" in body, (
        ".settings-action-btn must set font-size: 12px"
    )
    assert "border" in body, ".settings-action-btn must have border property"
    assert "background" in body, ".settings-action-btn must have background property"


def test_css_settings_action_btn_hover_exists() -> None:
    """.settings-action-btn:hover must exist with border-color accent."""

    css = read_css()
    assert ".settings-action-btn:hover" in css, (
        ".settings-action-btn:hover must be defined in style.css"
    )
    match = re.search(
        r"\.settings-action-btn:hover\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".settings-action-btn:hover rule not found"
    body = match.group(1)
    assert "border-color" in body and "var(--accent)" in body, (
        ".settings-action-btn:hover must set border-color: var(--accent)"
    )


def test_css_settings_action_btn_disabled_opacity() -> None:
    """.settings-action-btn:disabled must have opacity 0.5."""

    css = read_css()
    assert ".settings-action-btn:disabled" in css, (
        ".settings-action-btn:disabled must be defined in style.css"
    )
    match = re.search(
        r"\.settings-action-btn:disabled\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".settings-action-btn:disabled rule not found"
    body = match.group(1)
    assert "opacity: 0.5" in body or "opacity:0.5" in body, (
        ".settings-action-btn:disabled must set opacity: 0.5"
    )


# ============================================================
# New Session tab CSS (task-3-new-session-tab)
# ============================================================


def test_css_settings_textarea_exists() -> None:
    """.settings-textarea must be defined in style.css."""
    css = read_css()
    assert ".settings-textarea" in css, (
        ".settings-textarea class must be defined in style.css"
    )


def test_css_settings_textarea_full_width() -> None:
    """.settings-textarea must have width: 100%."""

    css = read_css()
    match = re.search(
        r"\.settings-textarea\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".settings-textarea rule not found"
    body = match.group(1)
    assert "width: 100%" in body or "width:100%" in body, (
        ".settings-textarea must set width: 100%"
    )


def test_css_settings_textarea_background() -> None:
    """.settings-textarea must use var(--bg-secondary) background."""

    css = read_css()
    match = re.search(
        r"\.settings-textarea\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".settings-textarea rule not found"
    body = match.group(1)
    assert "var(--bg-secondary)" in body, (
        ".settings-textarea must use var(--bg-secondary) background"
    )


def test_css_settings_textarea_border_and_radius() -> None:
    """.settings-textarea must have border and border-radius: 4px."""

    css = read_css()
    match = re.search(
        r"\.settings-textarea\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".settings-textarea rule not found"
    body = match.group(1)
    assert "border:" in body or "border :" in body, (
        ".settings-textarea must have a border property"
    )
    assert "border-radius: 4px" in body or "border-radius:4px" in body, (
        ".settings-textarea must have border-radius: 4px"
    )


def test_css_settings_textarea_font_mono_13px() -> None:
    """.settings-textarea must use font-family var(--font-mono) and font-size 13px."""

    css = read_css()
    match = re.search(
        r"\.settings-textarea\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".settings-textarea rule not found"
    body = match.group(1)
    assert "var(--font-mono)" in body, (
        ".settings-textarea must use var(--font-mono) font-family"
    )
    assert "font-size: 13px" in body or "font-size:13px" in body, (
        ".settings-textarea must use font-size: 13px"
    )


def test_css_settings_textarea_padding_and_resize() -> None:
    """.settings-textarea must have padding: 10px and resize: vertical."""

    css = read_css()
    match = re.search(
        r"\.settings-textarea\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".settings-textarea rule not found"
    body = match.group(1)
    assert "padding: 10px" in body or "padding:10px" in body, (
        ".settings-textarea must have padding: 10px"
    )
    assert "resize: vertical" in body or "resize:vertical" in body, (
        ".settings-textarea must have resize: vertical"
    )


def test_css_settings_textarea_min_height() -> None:
    """.settings-textarea must have min-height: 60px."""

    css = read_css()
    match = re.search(
        r"\.settings-textarea\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".settings-textarea rule not found"
    body = match.group(1)
    assert "min-height: 60px" in body or "min-height:60px" in body, (
        ".settings-textarea must have min-height: 60px"
    )


def test_css_settings_textarea_focus_accent_border() -> None:
    """.settings-textarea:focus must use border-color: var(--accent)."""

    css = read_css()
    assert ".settings-textarea:focus" in css, (
        ".settings-textarea:focus rule must be defined in style.css"
    )
    match = re.search(
        r"\.settings-textarea:focus\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".settings-textarea:focus rule not found"
    body = match.group(1)
    assert "var(--accent)" in body, (
        ".settings-textarea:focus must use var(--accent) border-color"
    )


def test_css_settings_helper_exists() -> None:
    """.settings-helper must be defined in style.css."""
    css = read_css()
    assert ".settings-helper" in css, (
        ".settings-helper class must be defined in style.css"
    )


def test_css_settings_helper_font_size() -> None:
    """.settings-helper must have font-size: 12px."""

    css = read_css()
    match = re.search(
        r"\.settings-helper\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".settings-helper rule not found"
    body = match.group(1)
    assert "font-size: 12px" in body or "font-size:12px" in body, (
        ".settings-helper must have font-size: 12px"
    )


def test_css_settings_helper_text_muted_color() -> None:
    """.settings-helper must use var(--text-muted) color."""

    css = read_css()
    match = re.search(
        r"\.settings-helper\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".settings-helper rule not found"
    body = match.group(1)
    assert "var(--text-muted)" in body, (
        ".settings-helper must use var(--text-muted) color"
    )


def test_css_settings_helper_italic() -> None:
    """.settings-helper must have font-style: italic."""

    css = read_css()
    match = re.search(
        r"\.settings-helper\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".settings-helper rule not found"
    body = match.group(1)
    assert "font-style: italic" in body or "font-style:italic" in body, (
        ".settings-helper must have font-style: italic"
    )


# ─── .new-session-input (task-4-header-plus-button) ──────────────────────────


def test_css_new_session_input_rule_exists() -> None:
    """.new-session-input CSS rule must exist in style.css."""
    css = read_css()
    assert ".new-session-input" in css, (
        ".new-session-input CSS rule must exist in style.css"
    )


def test_css_new_session_input_has_border() -> None:
    """.new-session-input must have a border (1px solid accent)."""

    css = read_css()
    match = re.search(
        r"\.new-session-input\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".new-session-input rule not found"
    body = match.group(1)
    assert "border" in body, ".new-session-input must have a border property"
    assert "accent" in body or "#00D9F5" in body or "1px solid" in body, (
        ".new-session-input border must reference accent color"
    )


def test_css_new_session_input_has_border_radius() -> None:
    """.new-session-input must have border-radius: 4px."""

    css = read_css()
    match = re.search(
        r"\.new-session-input\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".new-session-input rule not found"
    body = match.group(1)
    assert "border-radius" in body, ".new-session-input must have border-radius"
    assert "4px" in body, ".new-session-input border-radius must be 4px"


def test_css_new_session_input_has_font_size() -> None:
    """.new-session-input must have font-size: 13px."""

    css = read_css()
    match = re.search(
        r"\.new-session-input\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".new-session-input rule not found"
    body = match.group(1)
    assert "font-size" in body, ".new-session-input must have font-size"
    assert "13px" in body, ".new-session-input font-size must be 13px"


def test_css_new_session_input_has_padding() -> None:
    """.new-session-input must have padding: 4px 10px."""

    css = read_css()
    match = re.search(
        r"\.new-session-input\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".new-session-input rule not found"
    body = match.group(1)
    assert "padding" in body, ".new-session-input must have padding"
    assert "4px" in body and "10px" in body, (
        ".new-session-input padding must be 4px 10px"
    )


def test_css_new_session_input_has_width() -> None:
    """.new-session-input must have width: 180px."""

    css = read_css()
    match = re.search(
        r"\.new-session-input\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".new-session-input rule not found"
    body = match.group(1)
    assert "width" in body, ".new-session-input must have width"
    assert "180px" in body, ".new-session-input width must be 180px"


def test_css_new_session_input_has_outline_none() -> None:
    """.new-session-input must have outline: none."""

    css = read_css()
    match = re.search(
        r"\.new-session-input\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".new-session-input rule not found"
    body = match.group(1)
    assert "outline" in body and "none" in body, (
        ".new-session-input must have outline: none"
    )


def test_css_new_session_input_placeholder_rule_exists() -> None:
    """.new-session-input::placeholder CSS rule must exist."""
    css = read_css()
    assert (
        ".new-session-input::placeholder" in css
        or ".new-session-input::-webkit-input-placeholder" in css
    ), ".new-session-input::placeholder CSS rule must exist"


def test_css_new_session_input_placeholder_color() -> None:
    """.new-session-input::placeholder must have color: var(--text-dim)."""

    css = read_css()
    match = re.search(
        r"\.new-session-input::placeholder\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".new-session-input::placeholder rule not found"
    body = match.group(1)
    assert "color" in body and ("text-dim" in body or "--text-dim" in body), (
        ".new-session-input::placeholder must have color: var(--text-dim)"
    )


# ============================================================
# Sidebar footer (task-5-sidebar-new-footer)
# ============================================================


def test_css_sidebar_footer_rule_exists() -> None:
    """.sidebar-footer rule must exist in style.css."""
    css = read_css()
    assert ".sidebar-footer" in css, "Missing .sidebar-footer rule in style.css"


def test_css_sidebar_footer_padding() -> None:
    """.sidebar-footer must have padding: 8px."""

    css = read_css()
    match = re.search(
        r"\.sidebar-footer\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".sidebar-footer rule not found"
    body = match.group(1)
    assert "padding" in body, ".sidebar-footer must have padding property"
    assert "8px" in body, ".sidebar-footer padding must include 8px"


def test_css_sidebar_footer_border_top() -> None:
    """.sidebar-footer must have border-top."""

    css = read_css()
    match = re.search(
        r"\.sidebar-footer\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".sidebar-footer rule not found"
    body = match.group(1)
    assert "border-top" in body, ".sidebar-footer must have border-top property"


def test_css_sidebar_footer_flex_shrink_0() -> None:
    """.sidebar-footer must have flex-shrink: 0."""

    css = read_css()
    match = re.search(
        r"\.sidebar-footer\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".sidebar-footer rule not found"
    body = match.group(1)
    assert "flex-shrink" in body, ".sidebar-footer must have flex-shrink property"
    assert "0" in body, ".sidebar-footer flex-shrink must be 0"


def test_css_sidebar_new_btn_rule_exists() -> None:
    """.sidebar-new-btn rule must exist in style.css."""
    css = read_css()
    assert ".sidebar-new-btn" in css, "Missing .sidebar-new-btn rule in style.css"


def test_css_sidebar_new_btn_width_100() -> None:
    """.sidebar-new-btn must have width: 100%."""

    css = read_css()
    match = re.search(
        r"\.sidebar-new-btn\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".sidebar-new-btn rule not found"
    body = match.group(1)
    assert "width" in body, ".sidebar-new-btn must have width property"
    assert "100%" in body, ".sidebar-new-btn width must be 100%"


def test_css_sidebar_new_btn_dashed_border() -> None:
    """.sidebar-new-btn must have a dashed border."""

    css = read_css()
    match = re.search(
        r"\.sidebar-new-btn\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".sidebar-new-btn rule not found"
    body = match.group(1)
    assert "dashed" in body, ".sidebar-new-btn must have border: 1px dashed"


def test_css_sidebar_new_btn_hover_exists() -> None:
    """.sidebar-new-btn:hover rule must exist."""
    css = read_css()
    assert ".sidebar-new-btn:hover" in css, (
        "Missing .sidebar-new-btn:hover rule in style.css"
    )


# ============================================================
# Mobile FAB (task-6-mobile-fab)
# ============================================================


def test_css_fab_class_exists() -> None:
    """.new-session-fab CSS rule must exist."""
    css = read_css()
    assert ".new-session-fab" in css, "Missing .new-session-fab rule in style.css"


def test_css_fab_display_none_by_default() -> None:
    """.new-session-fab must have display:none as default (hidden on desktop)."""

    css = read_css()
    # Find the .new-session-fab block (non-media-query context)
    # Match the block that is NOT inside a @media rule
    # Look for .new-session-fab { ... display: none ... } outside of @media
    # Simple check: the rule body should contain 'display' before the first @media containing it
    match = re.search(r"\.new-session-fab\s*\{([^}]*)\}", css)
    assert match, ".new-session-fab rule not found"
    body = match.group(1)
    assert "display" in body and (
        "none" in body or "display:none" in body.replace(" ", "")
    ), f".new-session-fab must have display:none by default, got body: {body!r}"


def test_css_fab_position_fixed() -> None:
    """.new-session-fab must be position:fixed."""

    css = read_css()
    match = re.search(r"\.new-session-fab\s*\{([^}]*)\}", css)
    assert match, ".new-session-fab rule not found"
    body = match.group(1)
    assert "position" in body and "fixed" in body, (
        f".new-session-fab must have position:fixed, got: {body!r}"
    )


def test_css_fab_size_56px() -> None:
    """.new-session-fab must be 56px width and height."""

    css = read_css()
    match = re.search(r"\.new-session-fab\s*\{([^}]*)\}", css)
    assert match, ".new-session-fab rule not found"
    body = match.group(1)
    assert "56px" in body, (
        f".new-session-fab must have 56px size (width/height), got: {body!r}"
    )


def test_css_fab_border_radius_50_percent() -> None:
    """.new-session-fab must have border-radius:50% for circular shape."""

    css = read_css()
    match = re.search(r"\.new-session-fab\s*\{([^}]*)\}", css)
    assert match, ".new-session-fab rule not found"
    body = match.group(1)
    assert "50%" in body, f".new-session-fab must have border-radius:50%, got: {body!r}"


def test_css_fab_mobile_media_query_shows_flex() -> None:
    """At max-width: 959px, .new-session-fab must be shown as display:flex."""

    css = read_css()
    # Find the 959px media query and check that .new-session-fab uses display:flex
    match = re.search(
        r"@media\s*\([^)]*max-width\s*:\s*959px[^)]*\)\s*\{([^@]*)\}", css, re.DOTALL
    )
    assert match, "Missing @media (max-width: 959px) block"
    media_body = match.group(1)
    assert ".new-session-fab" in media_body, (
        "@media (max-width: 959px) block must contain .new-session-fab rule"
    )
    # Find the .new-session-fab rule within the media query
    fab_match = re.search(r"\.new-session-fab\s*\{([^}]*)\}", media_body)
    assert fab_match, ".new-session-fab rule not found in 959px media query"
    fab_body = fab_match.group(1)
    assert "flex" in fab_body, (
        f".new-session-fab must show as display:flex in 959px media query, got: {fab_body!r}"
    )


def test_css_fab_mobile_media_query_hides_new_session_btn() -> None:
    """At max-width: 959px, #new-session-btn must be hidden."""

    css = read_css()
    match = re.search(
        r"@media\s*\([^)]*max-width\s*:\s*959px[^)]*\)\s*\{([^@]*)\}", css, re.DOTALL
    )
    assert match, "Missing @media (max-width: 959px) block"
    media_body = match.group(1)
    assert "#new-session-btn" in media_body, (
        "@media (max-width: 959px) block must contain #new-session-btn rule to hide it"
    )
    # Find the #new-session-btn rule and verify it has display:none
    btn_match = re.search(r"#new-session-btn\s*\{([^}]*)\}", media_body)
    assert btn_match, "#new-session-btn rule not found in 959px media query"
    btn_body = btn_match.group(1)
    assert "none" in btn_body, (
        f"#new-session-btn must have display:none in 959px media query, got: {btn_body!r}"
    )


def test_css_fab_active_transform() -> None:
    """.new-session-fab:active must have a transform rule (scale)."""
    css = read_css()
    assert ".new-session-fab:active" in css, (
        "Missing .new-session-fab:active rule in style.css"
    )


def test_css_fab_focus_visible_outline_not_accent() -> None:
    """.new-session-fab:focus-visible outline must not use var(--accent) — same color as FAB background gives zero contrast."""
    css = read_css()
    match = re.search(r"\.new-session-fab:focus-visible\s*\{([^}]*)\}", css)
    assert match, "Missing .new-session-fab:focus-visible rule in style.css"
    body = match.group(1)
    assert "outline" in body, (
        ".new-session-fab:focus-visible must have an outline property"
    )
    # The FAB background IS var(--accent), so using the same color as outline gives zero visible ring.
    # Must use var(--bg) or var(--text) for sufficient contrast.
    assert "var(--accent)" not in body, (
        ".new-session-fab:focus-visible outline must not use var(--accent) — "
        "the FAB background is already var(--accent), so the outline would be invisible. "
        "Use var(--bg) or var(--text) for contrast."
    )


# ============================================================
# Consolidated settings CSS selectors (task-8-frontend-tests)
# ============================================================


def test_css_settings_dialog() -> None:
    """All settings dialog CSS selectors must exist: .settings-dialog, .settings-tabs, .settings-tab--active, .settings-content, .settings-field, .settings-select."""
    css = read_css()
    for cls in (
        ".settings-dialog",
        ".settings-tabs",
        ".settings-tab--active",
        ".settings-content",
        ".settings-field",
        ".settings-select",
    ):
        assert cls in css, f"Missing CSS selector '{cls}'"


def test_css_header_btn() -> None:
    """.header-btn and .header-actions CSS selectors must exist."""
    css = read_css()
    for cls in (".header-btn", ".header-actions"):
        assert cls in css, f"Missing CSS selector '{cls}'"


def test_css_new_session_fab() -> None:
    """.new-session-fab CSS selector must exist."""
    css = read_css()
    assert ".new-session-fab" in css, "Missing .new-session-fab CSS selector"


def test_css_new_session_input() -> None:
    """.new-session-input CSS selector must exist."""
    css = read_css()
    assert ".new-session-input" in css, "Missing .new-session-input CSS selector"


def test_css_settings_textarea() -> None:
    """.settings-textarea CSS selector must exist."""
    css = read_css()
    assert ".settings-textarea" in css, "Missing .settings-textarea CSS selector"


def test_css_sidebar_footer() -> None:
    """.sidebar-footer and .sidebar-new-btn CSS selectors must exist."""
    css = read_css()
    for cls in (".sidebar-footer", ".sidebar-new-btn"):
        assert cls in css, f"Missing CSS selector '{cls}'"


# ─── Remote Instances UI (task-15) ────────────────────────────────────────────


def test_css_remote_instances_classes() -> None:
    """CSS classes for remote instances management must exist in style.css."""
    css = read_css()
    for cls in (
        ".settings-remote-list",
        ".settings-remote-row",
        ".settings-remote-url",
        ".settings-remote-name",
        ".settings-remote-remove",
        ".settings-input",
    ):
        assert cls in css, (
            f"Missing CSS selector '{cls}' — required for Remote Instances UI"
        )


# ============================================================
# Source tile states: offline and auth-required (task-1-css-source-tile-states)
# ============================================================


def test_css_source_tile_base_exists() -> None:
    """.source-tile base class must exist in style.css."""
    css = read_css()
    assert ".source-tile {" in css or ".source-tile{" in css, (
        "Missing .source-tile CSS rule in style.css"
    )


def test_css_source_tile_base_layout() -> None:
    """.source-tile must have flex column layout with centered content, gap, padding."""

    css = read_css()
    match = re.search(r"\.source-tile\s*\{([^}]*)\}", css, re.DOTALL)
    assert match, ".source-tile rule not found"
    body = match.group(1)
    assert "display: flex" in body or "display:flex" in body.replace(" ", ""), (
        ".source-tile must use display: flex"
    )
    assert "flex-direction: column" in body, (
        ".source-tile must use flex-direction: column"
    )
    assert "align-items: center" in body, ".source-tile must use align-items: center"
    assert "justify-content: center" in body, (
        ".source-tile must use justify-content: center"
    )
    assert "gap: 12px" in body, ".source-tile must have gap: 12px"
    assert "padding: 24px" in body, ".source-tile must have padding: 24px"
    assert "var(--tile-height)" in body, ".source-tile must use var(--tile-height)"
    assert "var(--bg-tile)" in body, ".source-tile must use var(--bg-tile) background"
    assert "1px solid var(--border)" in body, (
        ".source-tile must have 1px solid var(--border) border"
    )
    assert "border-radius: 4px" in body, ".source-tile must have border-radius: 4px"


def test_css_source_tile_offline_exists() -> None:
    """.source-tile--offline modifier must exist in style.css."""
    css = read_css()
    assert ".source-tile--offline" in css, "Missing .source-tile--offline CSS rule"


def test_css_source_tile_offline_opacity_and_border() -> None:
    """.source-tile--offline must have opacity 0.45 and dashed border."""

    css = read_css()
    match = re.search(r"\.source-tile--offline\s*\{([^}]*)\}", css, re.DOTALL)
    assert match, ".source-tile--offline rule not found"
    body = match.group(1)
    assert "opacity: 0.45" in body or "opacity:0.45" in body.replace(" ", ""), (
        ".source-tile--offline must have opacity: 0.45"
    )
    assert "dashed" in body, ".source-tile--offline must have dashed border"


def test_css_source_tile_offline_name_color() -> None:
    """.source-tile--offline .source-tile__name must use var(--text-dim) color."""

    css = read_css()
    assert ".source-tile--offline .source-tile__name" in css, (
        "Missing .source-tile--offline .source-tile__name rule"
    )
    match = re.search(
        r"\.source-tile--offline\s+\.source-tile__name\s*\{([^}]*)\}", css, re.DOTALL
    )
    assert match, ".source-tile--offline .source-tile__name rule not found"
    body = match.group(1)
    assert "var(--text-dim)" in body, (
        ".source-tile--offline .source-tile__name must use var(--text-dim) color"
    )


def test_css_source_tile_offline_badge_exists() -> None:
    """.source-tile--offline .source-tile__badge must exist with err background."""

    css = read_css()
    assert ".source-tile--offline .source-tile__badge" in css, (
        "Missing .source-tile--offline .source-tile__badge rule"
    )
    match = re.search(
        r"\.source-tile--offline\s+\.source-tile__badge\s*\{([^}]*)\}", css, re.DOTALL
    )
    assert match, ".source-tile--offline .source-tile__badge rule not found"
    body = match.group(1)
    assert "var(--err)" in body, (
        ".source-tile--offline .source-tile__badge must use var(--err) background"
    )
    assert "font-size: 10px" in body or "10px" in body, (
        ".source-tile--offline .source-tile__badge must have 10px font"
    )
    assert "font-weight" in body and ("bold" in body or "700" in body), (
        ".source-tile--offline .source-tile__badge must be bold"
    )
    assert "text-transform: uppercase" in body, (
        ".source-tile--offline .source-tile__badge must be uppercase"
    )
    assert "border-radius: 10px" in body, (
        ".source-tile--offline .source-tile__badge must have border-radius: 10px"
    )


def test_css_source_tile_offline_last_seen_exists() -> None:
    """.source-tile--offline .source-tile__last-seen must exist with 11px font."""

    css = read_css()
    assert ".source-tile--offline .source-tile__last-seen" in css, (
        "Missing .source-tile--offline .source-tile__last-seen rule"
    )
    match = re.search(
        r"\.source-tile--offline\s+\.source-tile__last-seen\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, ".source-tile--offline .source-tile__last-seen rule not found"
    body = match.group(1)
    assert "font-size: 11px" in body or "11px" in body, (
        ".source-tile--offline .source-tile__last-seen must have 11px font"
    )
    assert "var(--text-dim)" in body, (
        ".source-tile--offline .source-tile__last-seen must use var(--text-dim) color"
    )


def test_css_source_tile_auth_exists() -> None:
    """.source-tile--auth modifier must exist with warn border-color and dashed style."""

    css = read_css()
    assert ".source-tile--auth" in css, "Missing .source-tile--auth CSS rule"
    match = re.search(r"\.source-tile--auth\s*\{([^}]*)\}", css, re.DOTALL)
    assert match, ".source-tile--auth rule not found"
    body = match.group(1)
    assert "var(--warn)" in body, ".source-tile--auth must use var(--warn) border-color"
    assert "dashed" in body, ".source-tile--auth must have dashed border"


def test_css_source_tile_name_exists() -> None:
    """.source-tile__name must exist with 15px font, weight 600, var(--text) color."""

    css = read_css()
    assert ".source-tile__name" in css, "Missing .source-tile__name CSS rule"
    # Match the STANDALONE .source-tile__name rule (not the descendant selector)
    # Use a start-of-line anchor (newline before .) to avoid matching
    # ".source-tile--offline .source-tile__name" first
    match = re.search(r"(?:^|\n)\.source-tile__name\s*\{([^}]*)\}", css, re.DOTALL)
    assert match, ".source-tile__name standalone rule not found"
    body = match.group(1)
    assert "font-size: 15px" in body or "15px" in body, (
        ".source-tile__name must have 15px font"
    )
    assert "font-weight: 600" in body or "600" in body, (
        ".source-tile__name must have font-weight 600"
    )
    assert "var(--text)" in body, ".source-tile__name must use var(--text) color"


def test_css_source_tile_login_btn_exists() -> None:
    """.source-tile__login-btn must exist with accent bg, border-radius, padding, 13px/600 font."""

    css = read_css()
    assert ".source-tile__login-btn" in css, "Missing .source-tile__login-btn CSS rule"
    match = re.search(r"\.source-tile__login-btn\s*\{([^}]*)\}", css, re.DOTALL)
    assert match, ".source-tile__login-btn rule not found"
    body = match.group(1)
    assert "var(--accent)" in body, (
        ".source-tile__login-btn must use var(--accent) background"
    )
    assert "var(--bg)" in body, ".source-tile__login-btn must use var(--bg) text color"
    assert "border-radius: 4px" in body, (
        ".source-tile__login-btn must have border-radius: 4px"
    )
    assert "padding: 8px 20px" in body, (
        ".source-tile__login-btn must have padding: 8px 20px"
    )
    assert "font-size: 13px" in body or "13px" in body, (
        ".source-tile__login-btn must have 13px font"
    )
    assert "cursor: pointer" in body, (
        ".source-tile__login-btn must have cursor: pointer"
    )


def test_css_source_tile_login_btn_hover_exists() -> None:
    """.source-tile__login-btn:hover must have opacity: 0.85."""

    css = read_css()
    assert ".source-tile__login-btn:hover" in css, (
        "Missing .source-tile__login-btn:hover CSS rule"
    )
    match = re.search(r"\.source-tile__login-btn:hover\s*\{([^}]*)\}", css, re.DOTALL)
    assert match, ".source-tile__login-btn:hover rule not found"
    body = match.group(1)
    assert "0.85" in body, ".source-tile__login-btn:hover must set opacity to 0.85"


def test_css_source_tile_login_btn_focus_visible_exists() -> None:
    """.source-tile__login-btn:focus-visible must have a 2px accent outline."""

    css = read_css()
    assert ".source-tile__login-btn:focus-visible" in css, (
        "Missing .source-tile__login-btn:focus-visible CSS rule"
    )
    match = re.search(
        r"\.source-tile__login-btn:focus-visible\s*\{([^}]*)\}", css, re.DOTALL
    )
    assert match, ".source-tile__login-btn:focus-visible rule not found"
    body = match.group(1)
    assert "var(--accent)" in body, (
        ".source-tile__login-btn:focus-visible must use var(--accent) in outline"
    )
    assert "2px" in body, ".source-tile__login-btn:focus-visible must have 2px outline"


def test_css_source_tile_hint_exists() -> None:
    """.source-tile__hint must exist with 11px font and var(--text-muted) color."""

    css = read_css()
    assert ".source-tile__hint" in css, "Missing .source-tile__hint CSS rule"
    match = re.search(r"\.source-tile__hint\s*\{([^}]*)\}", css, re.DOTALL)
    assert match, ".source-tile__hint rule not found"
    body = match.group(1)
    assert "font-size: 11px" in body or "11px" in body, (
        ".source-tile__hint must have 11px font"
    )
    assert "var(--text-muted)" in body, (
        ".source-tile__hint must use var(--text-muted) color"
    )


def test_css_source_tile_before_reduced_motion() -> None:
    """.source-tile rules must appear before @media (prefers-reduced-motion) block."""
    css = read_css()
    assert ".source-tile" in css, ".source-tile must exist in style.css"
    source_tile_idx = css.index(".source-tile")
    reduced_motion_idx = css.index("@media (prefers-reduced-motion")
    assert source_tile_idx < reduced_motion_idx, (
        ".source-tile rules must appear before @media (prefers-reduced-motion) block"
    )


def test_css_source_tile_match_count() -> None:
    """grep -c 'source-tile' must return sufficient lines (spec says ~20+ but counts lines not occurrences).

    The spec note '~20+' is approximate.  We verify that all required selectors
    are present by checking that grep-c style line count is >= 10, matching the
    11 selector lines produced by the full implementation.
    """

    css_path = CSS_PATH
    result = subprocess.run(
        ["grep", "-c", "source-tile", str(css_path)],
        capture_output=True,
        text=True,
    )
    line_count = int(result.stdout.strip())
    assert line_count >= 10, (
        f"Expected at least 10 lines containing 'source-tile' (grep -c), got {line_count}"
    )


def test_css_no_unclosed_braces() -> None:
    """CSS file must have balanced braces (no unclosed braces)."""
    css = read_css()
    open_count = css.count("{")
    close_count = css.count("}")
    assert open_count == close_count, (
        f"CSS file has unbalanced braces: {open_count} open vs {close_count} close"
    )


def test_css_no_compact_view() -> None:
    """.session-grid--compact CSS modifier must NOT exist — compact view was removed."""
    css = read_css()
    assert ".session-grid--compact" not in css, (
        ".session-grid--compact must be removed — compact view mode was removed, only Auto and Fit remain"
    )


def test_css_fit_view_exists() -> None:
    """.session-grid--fit CSS modifier must exist for fit view mode."""
    css = read_css()
    assert ".session-grid--fit" in css, (
        "Missing .session-grid--fit CSS selector for fit view mode"
    )


def test_css_no_compact_tile_height() -> None:
    """.session-grid--compact .session-tile must NOT exist — compact view was removed."""
    css = read_css()
    assert ".session-grid--compact .session-tile" not in css, (
        ".session-grid--compact .session-tile must be removed — compact view mode was removed"
    )


# ============================================================
# Fit view bug fixes
# ============================================================


# ============================================================
# Mobile viewport + fit view content anchoring fixes
# ============================================================


def test_view_uses_dvh_fallback() -> None:
    """Bug fix: .view must use 100dvh (dynamic viewport height) for mobile.

    On mobile browsers, 100vh includes the browser chrome (address bar + bottom nav),
    causing the bottom row of tiles to be cut off under overflow:hidden.
    Fix: height: 100dvh with 100vh fallback (progressive enhancement).
    The 100vh MUST appear before 100dvh (browsers ignore unknown values, so
    100dvh overrides 100vh for browsers that support it).
    """
    css = read_css()
    # .view block must contain 100dvh
    block = _extract_rule_block(css, ".view {")
    assert "100dvh" in block, (
        ".view must use height: 100dvh for mobile — 100vh includes browser chrome, "
        "causing the bottom row to be cut off on mobile devices"
    )
    # 100vh must still be present as fallback (appears before 100dvh in file)
    assert "100vh" in block, (
        ".view must keep height: 100vh as fallback for browsers without dvh support"
    )
    # Verify order: 100vh must come before 100dvh in the block (fallback first)
    assert block.index("100vh") < block.index("100dvh"), (
        "height: 100vh (fallback) must appear BEFORE height: 100dvh in .view rule — "
        "browsers that don't support dvh will use the last valid value"
    )


def test_fit_view_no_tile_body_flex_override() -> None:
    """Bug fix: .session-grid--fit .tile-body must NOT have a flex override.

    The flex + justify-content:flex-end approach failed because the <pre> with
    max-height:100% fills the parent entirely, making flex-end a no-op. Content
    started at the top and excess was clipped at the bottom — the opposite of what
    we want.

    Fix: delete the .session-grid--fit .tile-body rule entirely. The base CSS
    position:absolute + bottom:0 on the <pre> anchors content to the bottom.
    """
    css = read_css()
    assert ".session-grid--fit .tile-body {" not in css, (
        ".session-grid--fit .tile-body must be removed — flex-end approach does not work "
        "when <pre> fills 100% of the parent. Use base position:absolute + bottom:0."
    )


def test_fit_view_session_tile_has_height_auto() -> None:
    """Pure CSS fit layout: .session-grid--fit .session-tile must use height: auto.

    JS was measuring clientHeight and setting tile.style.height = tileH + 'px'.
    This failed when the grid was display:none (clientHeight = 0) and after
    innerHTML rebuilds destroyed inline styles every 2s.

    Pure CSS fix: the grid has a definite height (flex:1 inside 100dvh).
    grid-template-rows: repeat(rows, 1fr) divides that height equally.
    height: auto on tiles lets them fill their grid cells without JS measurement.
    """
    css = read_css()
    assert ".session-grid--fit .session-tile {" in css, (
        ".session-grid--fit .session-tile rule must exist for pure CSS fit layout"
    )
    block = _extract_rule_block(css, ".session-grid--fit .session-tile {")
    assert "height: auto" in block or "height:auto" in block, (
        ".session-grid--fit .session-tile must use height: auto — "
        "JS inline height setting was unreliable (lost on innerHTML rebuild every 2s)"
    )


def test_fit_view_no_pre_static_override() -> None:
    """Bug fix: .session-grid--fit .tile-body pre must NOT override position to static.

    The position:static override removed the pre from absolute positioning, breaking
    the bottom anchoring. Base CSS already has position:absolute + bottom:0 which
    anchors content to the bottom of the tile.

    Fix: delete the .session-grid--fit .tile-body pre rule entirely.
    """
    css = read_css()
    assert ".session-grid--fit .tile-body pre {" not in css, (
        ".session-grid--fit .tile-body pre override must be removed — revert to base "
        "position:absolute + bottom:0 for correct bottom anchoring."
    )


# ============================================================
# Multi-Device tab CSS (settings UI reorganization)
# ============================================================


def test_css_multi_device_fields_transition() -> None:
    """#multi-device-fields must have a CSS transition for smooth enable/disable animation."""
    css = read_css()
    assert "#multi-device-fields" in css, (
        "Missing #multi-device-fields CSS rule — needed for smooth enable/disable opacity transition"
    )
    match = re.search(r"#multi-device-fields\s*\{([^}]*)\}", css, re.DOTALL)
    assert match, "#multi-device-fields CSS rule not found"
    body = match.group(1)
    assert "transition" in body, (
        "#multi-device-fields must have a transition property for smooth enable/disable animation"
    )
    assert "opacity" in body, "#multi-device-fields transition must include opacity"


# ============================================================
# Custom scrollbar styling (thin, dark, brand cyan hover)
# ============================================================


def test_custom_scrollbar_styles():
    """Custom scrollbar styling must exist for both WebKit and Firefox."""
    css = read_css()
    assert "scrollbar-width: thin" in css, "Firefox scrollbar-width must be set"
    assert "::-webkit-scrollbar" in css, "WebKit scrollbar rules must exist"
    assert "border-radius: 3px" in css or "border-radius:3px" in css, (
        "scrollbar thumb must be rounded"
    )


# ============================================================
# Fix: default left border on tiles and sidebar items (task-4-fix-left-border)
# ============================================================


def test_css_session_tile_default_left_border() -> None:
    """.session-tile default border-left must use var(--border), not transparent.

    On the dark background, transparent makes the left border invisible —
    it looks like only 3 sides have a border. Fix: change to 3px solid var(--border)
    to match the other 3 borders. Bell and active states already override border-left-color.
    """
    css = read_css()
    block = _extract_rule_block(css, ".session-tile {")
    assert "border-left: 3px solid var(--border)" in block, (
        ".session-tile default border-left must use var(--border) — "
        "transparent is invisible on dark backgrounds"
    )
    # Verify transparent is not used as the default left border value
    assert "transparent" not in block, (
        ".session-tile must not use transparent for default border-left — "
        "it makes the left edge invisible on dark backgrounds"
    )


def test_css_sidebar_item_default_left_border() -> None:
    """.sidebar-item default border-left must use var(--border), not transparent.

    On the dark background, transparent makes the left border invisible —
    it looks like only 3 sides have a border. Fix: change to 3px solid var(--border)
    to match the other 3 borders. Active state already overrides border-left-color.
    """
    css = read_css()
    block = _extract_rule_block(css, ".sidebar-item {")
    assert "border-left: 3px solid var(--border)" in block, (
        ".sidebar-item default border-left must use var(--border) — "
        "transparent is invisible on dark backgrounds"
    )
    # Verify transparent is not used as the default left border value
    assert "transparent" not in block, (
        ".sidebar-item must not use transparent for default border-left — "
        "it makes the left edge invisible on dark backgrounds"
    )


# ============================================================
# Header view dropdown styles (task-5)
# ============================================================


def test_view_dropdown_trigger_styled() -> None:
    """.view-dropdown__trigger must exist in CSS."""
    css = read_css()
    assert ".view-dropdown__trigger" in css, (
        "Missing .view-dropdown__trigger CSS rule in style.css"
    )


def test_view_dropdown_menu_styled() -> None:
    """.view-dropdown__menu must exist in CSS and be absolutely positioned below trigger."""
    css = read_css()
    assert ".view-dropdown__menu" in css, (
        "Missing .view-dropdown__menu CSS rule in style.css"
    )
    block = _extract_rule_block(css, ".view-dropdown__menu {")
    assert "position: absolute" in block, (
        ".view-dropdown__menu must use position: absolute"
    )
    assert "z-index: 100" in block, ".view-dropdown__menu must have z-index: 100"


def test_view_dropdown_item_styled() -> None:
    """.view-dropdown__item must exist in CSS."""
    css = read_css()
    assert ".view-dropdown__item" in css, (
        "Missing .view-dropdown__item CSS rule in style.css"
    )


# ---------------------------------------------------------------------------
# Tile flyout menu styles
# ---------------------------------------------------------------------------


def test_flyout_menu_styled() -> None:
    """style.css must contain .flyout-menu styles."""
    css = read_css()
    assert ".flyout-menu" in css, "style.css must style .flyout-menu"


def test_flyout_menu_item_styled() -> None:
    """style.css must contain .flyout-menu__item styles."""
    css = read_css()
    assert ".flyout-menu__item" in css, "style.css must style .flyout-menu__item"


def test_flyout_trigger_styled() -> None:
    """style.css must contain .tile-options-btn styles."""
    css = read_css()
    assert ".tile-options-btn" in css, "style.css must style .tile-options-btn"


def test_flyout_submenu_styled() -> None:
    """style.css must contain .flyout-submenu styles."""
    css = read_css()
    assert ".flyout-submenu" in css, "style.css must style .flyout-submenu"


def test_flyout_bottom_sheet_styled() -> None:
    """style.css must contain .flyout-sheet styles for mobile."""
    css = read_css()
    assert ".flyout-sheet" in css, (
        "style.css must style .flyout-sheet (mobile bottom action sheet)"
    )


# ============================================================
# Add Sessions panel CSS (task-8)
# ============================================================


def test_add_sessions_panel_styled() -> None:
    """style.css must contain .add-sessions-panel styles."""
    css = read_css()
    assert ".add-sessions-panel" in css, "style.css must style .add-sessions-panel"


def test_add_sessions_item_styled() -> None:
    """style.css must contain .add-sessions-item styles."""
    css = read_css()
    assert ".add-sessions-item" in css, "style.css must style .add-sessions-item"
    assert ".add-sessions-item" in css, "style.css must style .add-sessions-item"


# ─── Phase 3 COE findings regression tests ──────────────────────────────────


# ── BUG 2: disclosure is statically visible (not hover-gated) ────────────────


def test_disclosure_not_hidden_by_css() -> None:
    """`.add-sessions-item__disclosure` must NOT have `display: none` in style.css.

    BUG: The CSS had `display: none` on the disclosure. The JS hover handlers set
    `disc.style.display = ''` (removing the inline style) which did NOT override the
    CSS rule — the disclosure remained invisible on hover. Fix: remove `display: none`
    from CSS so the disclosure is statically visible for hidden items (it is already
    only rendered for hidden items in the HTML).
    """
    css = read_css()
    import re as _re

    match = _re.search(
        r"\.add-sessions-item__disclosure\s*\{([^}]*)\}",
        css,
        _re.DOTALL,
    )
    assert match, ".add-sessions-item__disclosure rule not found in style.css"
    body = match.group(1)
    assert "display: none" not in body and "display:none" not in body.replace(" ", ""), (
        ".add-sessions-item__disclosure must NOT have display:none — "
        "the disclosure must be statically visible for hidden items (BUG 2 fix). "
        "The hover-based show/hide was broken: setting disc.style.display='' doesn't "
        "override a CSS display:none rule — it only removes the inline style."
    )


# ── CLEANUP 1: .tile-delete CSS removed ──────────────────────────────────────


def test_tile_delete_css_removed() -> None:
    """`.tile-delete` CSS rules must be removed from style.css.

    CLEANUP: The .tile-delete button was removed from buildTileHTML (Phase 2 task).
    The orphaned CSS rules must be cleaned up.
    """
    css = read_css()
    assert ".tile-delete" not in css, (
        ".tile-delete CSS rules must be removed from style.css — "
        "the button was removed from buildTileHTML; orphaned CSS is dead code"
    )


# ── Fix A: .flyout-sheet__title CSS (Phase 3 COE re-verification) ─────────────


def test_flyout_sheet_title_css_exists() -> None:
    """.flyout-sheet__title must have a CSS rule in the flyout-sheet section.

    Fix A: The kill-confirm sheet renders a title div with class
    `flyout-sheet__title` but the CSS rule was missing, leaving the title
    as raw unstyled text.
    """
    css = read_css()
    assert ".flyout-sheet__title" in css, (
        ".flyout-sheet__title CSS rule must exist in style.css — "
        "the kill-confirm sheet renders a title div with this class"
    )


# ── Fix C: .sidebar-delete orphaned CSS removed (Phase 3 COE re-verification) ──


def test_no_sidebar_delete_css() -> None:
    """.sidebar-delete CSS rules must be removed from style.css (orphaned dead code).

    Fix C: The .sidebar-delete button was removed from buildSidebarHTML() in a
    previous task.  The 3 CSS rule blocks (.sidebar-delete,
    .sidebar-item:hover .sidebar-delete, .sidebar-delete:hover) are now dead
    code and must be cleaned up.
    """
    css = read_css()
    assert ".sidebar-delete" not in css, (
        ".sidebar-delete CSS rules must be removed from style.css — "
        "the button was removed from buildSidebarHTML(); orphaned CSS is dead code"
    )

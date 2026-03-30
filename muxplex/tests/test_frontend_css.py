"""Tests for frontend/style.css — design tokens and dark theme."""

import pathlib
import re

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


def test_css_session_grid(css=None):
    css = read_css()
    assert "auto-fill" in css
    assert "minmax" in css


def test_css_tile_height(css=None):
    css = read_css()
    assert ".session-tile" in css
    assert "var(--tile-height)" in css


def test_css_bell_indicator(css=None):
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
    assert ".tile-bell-count" in css
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
    import re

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
    import re

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
    import re

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
    import re

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
    import re

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
    import re

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
    import re

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
    import re

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
    import re

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
    import re

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
    import re

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
    import re

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
    import re

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
    import re

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
    import re

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
    import re

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
    import re

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
    import re

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
    import re

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
    import re

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
    import re

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
    import re

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
    assert ".new-session-input::placeholder" in css or \
        ".new-session-input::-webkit-input-placeholder" in css, (
        ".new-session-input::placeholder CSS rule must exist"
    )


def test_css_new_session_input_placeholder_color() -> None:
    """.new-session-input::placeholder must have color: var(--text-dim)."""
    import re

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
    import re
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
    import re
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
    import re
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
    import re
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
    import re
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
    import re
    css = read_css()
    # Find the .new-session-fab block (non-media-query context)
    # Match the block that is NOT inside a @media rule
    # Look for .new-session-fab { ... display: none ... } outside of @media
    # Simple check: the rule body should contain 'display' before the first @media containing it
    match = re.search(r'\.new-session-fab\s*\{([^}]*)\}', css)
    assert match, ".new-session-fab rule not found"
    body = match.group(1)
    assert "display" in body and ("none" in body or "display:none" in body.replace(" ", "")), (
        f".new-session-fab must have display:none by default, got body: {body!r}"
    )


def test_css_fab_position_fixed() -> None:
    """.new-session-fab must be position:fixed."""
    import re
    css = read_css()
    match = re.search(r'\.new-session-fab\s*\{([^}]*)\}', css)
    assert match, ".new-session-fab rule not found"
    body = match.group(1)
    assert "position" in body and "fixed" in body, (
        f".new-session-fab must have position:fixed, got: {body!r}"
    )


def test_css_fab_size_56px() -> None:
    """.new-session-fab must be 56px width and height."""
    import re
    css = read_css()
    match = re.search(r'\.new-session-fab\s*\{([^}]*)\}', css)
    assert match, ".new-session-fab rule not found"
    body = match.group(1)
    assert "56px" in body, (
        f".new-session-fab must have 56px size (width/height), got: {body!r}"
    )


def test_css_fab_border_radius_50_percent() -> None:
    """.new-session-fab must have border-radius:50% for circular shape."""
    import re
    css = read_css()
    match = re.search(r'\.new-session-fab\s*\{([^}]*)\}', css)
    assert match, ".new-session-fab rule not found"
    body = match.group(1)
    assert "50%" in body, (
        f".new-session-fab must have border-radius:50%, got: {body!r}"
    )


def test_css_fab_mobile_media_query_shows_flex() -> None:
    """At max-width: 959px, .new-session-fab must be shown as display:flex."""
    import re
    css = read_css()
    # Find the 959px media query and check that .new-session-fab uses display:flex
    match = re.search(
        r'@media\s*\([^)]*max-width\s*:\s*959px[^)]*\)\s*\{([^@]*)\}',
        css,
        re.DOTALL
    )
    assert match, "Missing @media (max-width: 959px) block"
    media_body = match.group(1)
    assert ".new-session-fab" in media_body, (
        "@media (max-width: 959px) block must contain .new-session-fab rule"
    )
    # Find the .new-session-fab rule within the media query
    fab_match = re.search(r'\.new-session-fab\s*\{([^}]*)\}', media_body)
    assert fab_match, ".new-session-fab rule not found in 959px media query"
    fab_body = fab_match.group(1)
    assert "flex" in fab_body, (
        f".new-session-fab must show as display:flex in 959px media query, got: {fab_body!r}"
    )


def test_css_fab_mobile_media_query_hides_new_session_btn() -> None:
    """At max-width: 959px, #new-session-btn must be hidden."""
    import re
    css = read_css()
    match = re.search(
        r'@media\s*\([^)]*max-width\s*:\s*959px[^)]*\)\s*\{([^@]*)\}',
        css,
        re.DOTALL
    )
    assert match, "Missing @media (max-width: 959px) block"
    media_body = match.group(1)
    assert "#new-session-btn" in media_body, (
        "@media (max-width: 959px) block must contain #new-session-btn rule to hide it"
    )
    # Find the #new-session-btn rule and verify it has display:none
    btn_match = re.search(r'#new-session-btn\s*\{([^}]*)\}', media_body)
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
    assert "outline" in body, ".new-session-fab:focus-visible must have an outline property"
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

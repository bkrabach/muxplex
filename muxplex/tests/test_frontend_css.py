"""Tests for frontend/style.css — design tokens and dark theme."""

import pathlib

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


def test_css_command_palette():
    css = read_css()
    assert ".command-palette__dialog" in css
    assert ".command-palette__input" in css
    assert ".palette-item" in css
    assert ".palette-item--selected" in css


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
    """.sidebar-item--active must use inset box-shadow stripe instead of border-left-width to avoid layout shift."""
    css = read_css()
    assert ".sidebar-item--active" in css
    block = _extract_rule_block(css, ".sidebar-item--active {")
    assert "background: var(--bg-surface)" in block
    assert "border-color: var(--accent)" in block
    assert "box-shadow: inset 3px 0 0 var(--accent)" in block


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
    """.sidebar-item-body pre must be anchored to bottom with 10px monospace font using --font-mono token."""
    css = read_css()
    assert ".sidebar-item-body pre" in css
    block = _extract_rule_block(css, ".sidebar-item-body pre {")
    assert "position: absolute" in block
    assert "bottom: 0" in block
    assert "left: 0" in block
    assert "right: 0" in block
    assert "font-size: 10px" in block
    assert "line-height: 1.4" in block
    assert "color: var(--text-muted)" in block
    assert "white-space: pre" in block
    assert "padding: 0 8px 6px" in block
    # Monospace font family via design token
    assert "font-family: var(--font-mono)" in block


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

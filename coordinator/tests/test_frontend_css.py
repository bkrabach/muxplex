"""Tests for frontend/style.css — design tokens and dark theme."""

import pathlib

CSS_PATH = pathlib.Path(__file__).parent.parent.parent / "frontend" / "style.css"


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
    assert sidebar_idx < terminal_idx, ".session-sidebar must come before .terminal-container"


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

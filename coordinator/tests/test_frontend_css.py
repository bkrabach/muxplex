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

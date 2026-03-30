"""Tests for frontend/app.js — verifies palette code removal and handleGlobalKeydown simplification."""

import pathlib
import re

JS_PATH = pathlib.Path(__file__).parent.parent / "frontend" / "app.js"

# Read once per module — tests are read-only so sharing is safe.
_JS: str = JS_PATH.read_text()


# ── Palette state variables must be removed ──────────────────────────────────

def test_no_palette_max_items_constant() -> None:
    """PALETTE_MAX_ITEMS constant must be removed."""
    assert "PALETTE_MAX_ITEMS" not in _JS, (
        "PALETTE_MAX_ITEMS must be removed from app.js"
    )


def test_no_palette_selected_index_variable() -> None:
    """_paletteSelectedIndex variable must be removed."""
    assert "_paletteSelectedIndex" not in _JS, (
        "_paletteSelectedIndex must be removed from app.js"
    )


def test_no_palette_filtered_sessions_variable() -> None:
    """_paletteFilteredSessions variable must be removed."""
    assert "_paletteFilteredSessions" not in _JS, (
        "_paletteFilteredSessions must be removed from app.js"
    )


def test_no_palette_open_variable() -> None:
    """_paletteOpen variable must be removed."""
    assert "_paletteOpen" not in _JS, (
        "_paletteOpen must be removed from app.js"
    )


def test_no_palette_input_listener_variable() -> None:
    """_paletteInputListener variable must be removed."""
    assert "_paletteInputListener" not in _JS, (
        "_paletteInputListener must be removed from app.js"
    )


# ── Palette functions must be removed ────────────────────────────────────────

def test_no_render_palette_list_function() -> None:
    """renderPaletteList function must be removed."""
    assert "renderPaletteList" not in _JS, (
        "renderPaletteList must be removed from app.js"
    )


def test_no_highlight_palette_item_function() -> None:
    """highlightPaletteItem function must be removed."""
    assert "highlightPaletteItem" not in _JS, (
        "highlightPaletteItem must be removed from app.js"
    )


def test_no_open_palette_function() -> None:
    """openPalette function must be removed."""
    assert "openPalette" not in _JS, (
        "openPalette must be removed from app.js"
    )


def test_no_close_palette_function() -> None:
    """closePalette function must be removed."""
    assert "closePalette" not in _JS, (
        "closePalette must be removed from app.js"
    )


def test_no_on_palette_input_function() -> None:
    """onPaletteInput function must be removed."""
    assert "onPaletteInput" not in _JS, (
        "onPaletteInput must be removed from app.js"
    )


def test_no_handle_palette_keydown_function() -> None:
    """handlePaletteKeydown function must be removed."""
    assert "handlePaletteKeydown" not in _JS, (
        "handlePaletteKeydown must be removed from app.js"
    )


# ── handleGlobalKeydown must be simplified ───────────────────────────────────

def test_handle_global_keydown_exists() -> None:
    """handleGlobalKeydown function must exist."""
    assert "function handleGlobalKeydown" in _JS, (
        "handleGlobalKeydown must still exist in app.js"
    )


def test_handle_global_keydown_no_palette_open_check() -> None:
    """handleGlobalKeydown must not check _paletteOpen."""
    # Extract the function body
    match = re.search(
        r"function handleGlobalKeydown\s*\(e\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "handleGlobalKeydown function not found"
    body = match.group(1)
    assert "_paletteOpen" not in body, (
        "handleGlobalKeydown must not reference _paletteOpen"
    )


def test_handle_global_keydown_no_open_palette_call() -> None:
    """handleGlobalKeydown must not call openPalette."""
    match = re.search(
        r"function handleGlobalKeydown\s*\(e\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "handleGlobalKeydown function not found"
    body = match.group(1)
    assert "openPalette" not in body, (
        "handleGlobalKeydown must not call openPalette"
    )


def test_handle_global_keydown_handles_escape_in_fullscreen() -> None:
    """handleGlobalKeydown must call closeSession() on Escape in fullscreen mode."""
    match = re.search(
        r"function handleGlobalKeydown\s*\(e\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "handleGlobalKeydown function not found"
    body = match.group(1)
    assert "fullscreen" in body, "handleGlobalKeydown must check for fullscreen mode"
    assert "Escape" in body, "handleGlobalKeydown must handle Escape key"
    assert "closeSession" in body, "handleGlobalKeydown must call closeSession"


# ── bindStaticEventListeners must have no palette references ─────────────────

def test_bind_static_event_listeners_no_palette_trigger() -> None:
    """bindStaticEventListeners must not bind palette-trigger click."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "palette-trigger" not in body, (
        "bindStaticEventListeners must not bind palette-trigger click"
    )


def test_bind_static_event_listeners_no_palette_backdrop() -> None:
    """bindStaticEventListeners must not bind palette-backdrop click."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "palette-backdrop" not in body, (
        "bindStaticEventListeners must not bind palette-backdrop click"
    )


# ── Palette test-only helpers must be removed ─────────────────────────────────

def test_no_set_palette_filtered_sessions_helper() -> None:
    """_setPaletteFilteredSessions test helper must be removed."""
    assert "_setPaletteFilteredSessions" not in _JS, (
        "_setPaletteFilteredSessions must be removed from app.js"
    )


def test_no_get_palette_filtered_sessions_helper() -> None:
    """_getPaletteFilteredSessions test helper must be removed."""
    assert "_getPaletteFilteredSessions" not in _JS, (
        "_getPaletteFilteredSessions must be removed from app.js"
    )


def test_no_set_palette_selected_index_helper() -> None:
    """_setPaletteSelectedIndex test helper must be removed."""
    assert "_setPaletteSelectedIndex" not in _JS, (
        "_setPaletteSelectedIndex must be removed from app.js"
    )


def test_no_get_palette_selected_index_helper() -> None:
    """_getPaletteSelectedIndex test helper must be removed."""
    assert "_getPaletteSelectedIndex" not in _JS, (
        "_getPaletteSelectedIndex must be removed from app.js"
    )


def test_no_set_palette_open_helper() -> None:
    """_setPaletteOpen test helper must be removed."""
    assert "_setPaletteOpen" not in _JS, (
        "_setPaletteOpen must be removed from app.js"
    )


def test_no_is_palette_open_helper() -> None:
    """_isPaletteOpen test helper must be removed."""
    assert "_isPaletteOpen" not in _JS, (
        "_isPaletteOpen must be removed from app.js"
    )


# ── module.exports must not include palette exports ───────────────────────────

def test_exports_no_render_palette_list() -> None:
    """module.exports must not export renderPaletteList."""
    match = re.search(
        r"module\.exports\s*=\s*\{(.*?)\};",
        _JS,
        re.DOTALL,
    )
    assert match, "module.exports block not found"
    exports = match.group(1)
    assert "renderPaletteList" not in exports, (
        "module.exports must not export renderPaletteList"
    )


def test_exports_no_highlight_palette_item() -> None:
    """module.exports must not export highlightPaletteItem."""
    match = re.search(
        r"module\.exports\s*=\s*\{(.*?)\};",
        _JS,
        re.DOTALL,
    )
    assert match, "module.exports block not found"
    exports = match.group(1)
    assert "highlightPaletteItem" not in exports, (
        "module.exports must not export highlightPaletteItem"
    )


def test_exports_no_open_palette() -> None:
    """module.exports must not export openPalette."""
    match = re.search(
        r"module\.exports\s*=\s*\{(.*?)\};",
        _JS,
        re.DOTALL,
    )
    assert match, "module.exports block not found"
    exports = match.group(1)
    assert "openPalette" not in exports, (
        "module.exports must not export openPalette"
    )


def test_exports_no_close_palette() -> None:
    """module.exports must not export closePalette."""
    match = re.search(
        r"module\.exports\s*=\s*\{(.*?)\};",
        _JS,
        re.DOTALL,
    )
    assert match, "module.exports block not found"
    exports = match.group(1)
    assert "closePalette" not in exports, (
        "module.exports must not export closePalette"
    )


def test_exports_no_on_palette_input() -> None:
    """module.exports must not export onPaletteInput."""
    match = re.search(
        r"module\.exports\s*=\s*\{(.*?)\};",
        _JS,
        re.DOTALL,
    )
    assert match, "module.exports block not found"
    exports = match.group(1)
    assert "onPaletteInput" not in exports, (
        "module.exports must not export onPaletteInput"
    )


def test_exports_no_handle_palette_keydown() -> None:
    """module.exports must not export handlePaletteKeydown."""
    match = re.search(
        r"module\.exports\s*=\s*\{(.*?)\};",
        _JS,
        re.DOTALL,
    )
    assert match, "module.exports block not found"
    exports = match.group(1)
    assert "handlePaletteKeydown" not in exports, (
        "module.exports must not export handlePaletteKeydown"
    )


def test_exports_still_has_handle_global_keydown() -> None:
    """module.exports must still export handleGlobalKeydown."""
    match = re.search(
        r"module\.exports\s*=\s*\{(.*?)\};",
        _JS,
        re.DOTALL,
    )
    assert match, "module.exports block not found"
    exports = match.group(1)
    assert "handleGlobalKeydown" in exports, (
        "module.exports must still export handleGlobalKeydown"
    )


def test_exports_still_has_bind_static_event_listeners() -> None:
    """module.exports must still export bindStaticEventListeners."""
    match = re.search(
        r"module\.exports\s*=\s*\{(.*?)\};",
        _JS,
        re.DOTALL,
    )
    assert match, "module.exports block not found"
    exports = match.group(1)
    assert "bindStaticEventListeners" in exports, (
        "module.exports must still export bindStaticEventListeners"
    )

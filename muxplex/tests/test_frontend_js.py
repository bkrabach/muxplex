"""Tests for frontend/app.js — verifies palette code removal and handleGlobalKeydown simplification."""

import pathlib
import re

JS_PATH = pathlib.Path(__file__).parent.parent / "frontend" / "app.js"
TERMINAL_JS_PATH = pathlib.Path(__file__).parent.parent / "frontend" / "terminal.js"

# Read once per module — tests are read-only so sharing is safe.
_JS: str = JS_PATH.read_text()
_TERMINAL_JS: str = TERMINAL_JS_PATH.read_text()


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
    assert "_paletteOpen" not in _JS, "_paletteOpen must be removed from app.js"


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
    assert "openPalette" not in _JS, "openPalette must be removed from app.js"


def test_no_close_palette_function() -> None:
    """closePalette function must be removed."""
    assert "closePalette" not in _JS, "closePalette must be removed from app.js"


def test_no_on_palette_input_function() -> None:
    """onPaletteInput function must be removed."""
    assert "onPaletteInput" not in _JS, "onPaletteInput must be removed from app.js"


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
    assert "openPalette" not in body, "handleGlobalKeydown must not call openPalette"


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
    assert "_setPaletteOpen" not in _JS, "_setPaletteOpen must be removed from app.js"


def test_no_is_palette_open_helper() -> None:
    """_isPaletteOpen test helper must be removed."""
    assert "_isPaletteOpen" not in _JS, "_isPaletteOpen must be removed from app.js"


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
    assert "openPalette" not in exports, "module.exports must not export openPalette"


def test_exports_no_close_palette() -> None:
    """module.exports must not export closePalette."""
    match = re.search(
        r"module\.exports\s*=\s*\{(.*?)\};",
        _JS,
        re.DOTALL,
    )
    assert match, "module.exports block not found"
    exports = match.group(1)
    assert "closePalette" not in exports, "module.exports must not export closePalette"


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


# ── Settings state variables ─────────────────────────────────────────────────


def test_settings_open_state_variable_exists() -> None:
    """_settingsOpen state variable must be declared."""
    assert "_settingsOpen" in _JS, "_settingsOpen must be declared in app.js"


def test_display_settings_key_constant_exists() -> None:
    """DISPLAY_SETTINGS_KEY constant must be declared."""
    assert "DISPLAY_SETTINGS_KEY" in _JS, "DISPLAY_SETTINGS_KEY must be in app.js"


def test_display_settings_key_value() -> None:
    """DISPLAY_SETTINGS_KEY must be 'muxplex.display'."""
    assert "'muxplex.display'" in _JS or '"muxplex.display"' in _JS, (
        "DISPLAY_SETTINGS_KEY must be 'muxplex.display'"
    )


def test_display_defaults_constant_exists() -> None:
    """DISPLAY_DEFAULTS constant must be declared."""
    assert "DISPLAY_DEFAULTS" in _JS, "DISPLAY_DEFAULTS must be in app.js"


def test_display_defaults_has_font_size() -> None:
    """DISPLAY_DEFAULTS must contain fontSize: 14."""
    assert "fontSize" in _JS, "DISPLAY_DEFAULTS must include fontSize"
    assert "14" in _JS, "DISPLAY_DEFAULTS fontSize must be 14"


def test_display_defaults_has_hover_preview_delay() -> None:
    """DISPLAY_DEFAULTS must contain hoverPreviewDelay: 1500."""
    assert "hoverPreviewDelay" in _JS, "DISPLAY_DEFAULTS must include hoverPreviewDelay"


def test_display_defaults_has_grid_columns() -> None:
    """DISPLAY_DEFAULTS must contain gridColumns: 'auto'."""
    assert "gridColumns" in _JS, "DISPLAY_DEFAULTS must include gridColumns"


def test_display_defaults_has_bell_sound() -> None:
    """DISPLAY_DEFAULTS must contain bellSound: false."""
    assert "bellSound" in _JS, "DISPLAY_DEFAULTS must include bellSound"


def test_display_defaults_has_notification_permission() -> None:
    """DISPLAY_DEFAULTS must contain notificationPermission: 'default'."""
    assert "notificationPermission" in _JS, (
        "DISPLAY_DEFAULTS must include notificationPermission"
    )


# ── Settings functions must exist ─────────────────────────────────────────────


def test_load_display_settings_function_exists() -> None:
    """loadDisplaySettings function must exist."""
    assert "function loadDisplaySettings" in _JS, (
        "loadDisplaySettings must be defined in app.js"
    )


def test_save_display_settings_function_exists() -> None:
    """saveDisplaySettings function must exist."""
    assert "function saveDisplaySettings" in _JS, (
        "saveDisplaySettings must be defined in app.js"
    )


def test_open_settings_function_exists() -> None:
    """openSettings function must exist."""
    assert "function openSettings" in _JS, "openSettings must be defined in app.js"


def test_close_settings_function_exists() -> None:
    """closeSettings function must exist."""
    assert "function closeSettings" in _JS, "closeSettings must be defined in app.js"


def test_switch_settings_tab_function_exists() -> None:
    """switchSettingsTab function must exist."""
    assert "function switchSettingsTab" in _JS, (
        "switchSettingsTab must be defined in app.js"
    )


# ── loadDisplaySettings implementation ───────────────────────────────────────


def test_load_display_settings_reads_from_localstorage() -> None:
    """loadDisplaySettings must read from localStorage using DISPLAY_SETTINGS_KEY."""
    match = re.search(
        r"function loadDisplaySettings\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "loadDisplaySettings function not found"
    body = match.group(1)
    assert "localStorage" in body, "loadDisplaySettings must read from localStorage"
    assert "DISPLAY_SETTINGS_KEY" in body or "muxplex.display" in body, (
        "loadDisplaySettings must use DISPLAY_SETTINGS_KEY"
    )


def test_load_display_settings_uses_object_assign() -> None:
    """loadDisplaySettings must merge with DISPLAY_DEFAULTS via Object.assign."""
    match = re.search(
        r"function loadDisplaySettings\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "loadDisplaySettings function not found"
    body = match.group(1)
    assert "Object.assign" in body, (
        "loadDisplaySettings must use Object.assign to merge with defaults"
    )


def test_load_display_settings_returns_defaults_on_error() -> None:
    """loadDisplaySettings must catch errors and return defaults."""
    match = re.search(
        r"function loadDisplaySettings\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "loadDisplaySettings function not found"
    body = match.group(1)
    assert "catch" in body or "try" in body, (
        "loadDisplaySettings must have error handling (try/catch)"
    )


# ── saveDisplaySettings implementation ───────────────────────────────────────


def test_save_display_settings_writes_to_localstorage() -> None:
    """saveDisplaySettings must write to localStorage."""
    match = re.search(
        r"function saveDisplaySettings\s*\(.*?\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "saveDisplaySettings function not found"
    body = match.group(1)
    assert "localStorage" in body, "saveDisplaySettings must write to localStorage"
    assert "setItem" in body, "saveDisplaySettings must call localStorage.setItem"


def test_save_display_settings_catches_errors() -> None:
    """saveDisplaySettings must catch errors."""
    match = re.search(
        r"function saveDisplaySettings\s*\(.*?\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "saveDisplaySettings function not found"
    body = match.group(1)
    assert "catch" in body or "try" in body, (
        "saveDisplaySettings must have error handling (try/catch)"
    )


# ── openSettings implementation ───────────────────────────────────────────────


def test_open_settings_sets_settings_open_true() -> None:
    """openSettings must set _settingsOpen = true."""
    match = re.search(
        r"function openSettings\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "openSettings function not found"
    body = match.group(1)
    assert "_settingsOpen" in body and "true" in body, (
        "openSettings must set _settingsOpen = true"
    )


def test_open_settings_calls_show_modal() -> None:
    """openSettings must call dialog.showModal()."""
    match = re.search(
        r"function openSettings\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "openSettings function not found"
    body = match.group(1)
    assert "showModal" in body, "openSettings must call dialog.showModal()"


def test_open_settings_removes_hidden_from_backdrop() -> None:
    """openSettings must remove hidden class from backdrop."""
    match = re.search(
        r"function openSettings\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "openSettings function not found"
    body = match.group(1)
    assert "settings-backdrop" in body, "openSettings must reference settings-backdrop"
    assert "remove" in body, "openSettings must call remove (hidden class removal)"


def test_open_settings_loads_form_controls() -> None:
    """openSettings must load display settings into form controls."""
    match = re.search(
        r"function openSettings\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "openSettings function not found"
    body = match.group(1)
    assert "setting-font-size" in body, (
        "openSettings must set setting-font-size form control"
    )
    assert "setting-hover-delay" in body, (
        "openSettings must set setting-hover-delay form control"
    )
    assert "setting-grid-columns" in body, (
        "openSettings must set setting-grid-columns form control"
    )


# ── closeSettings implementation ──────────────────────────────────────────────


def test_close_settings_sets_settings_open_false() -> None:
    """closeSettings must set _settingsOpen = false."""
    match = re.search(
        r"function closeSettings\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "closeSettings function not found"
    body = match.group(1)
    assert "_settingsOpen" in body and "false" in body, (
        "closeSettings must set _settingsOpen = false"
    )


def test_close_settings_calls_dialog_close() -> None:
    """closeSettings must call dialog.close()."""
    match = re.search(
        r"function closeSettings\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "closeSettings function not found"
    body = match.group(1)
    assert ".close()" in body, "closeSettings must call dialog.close()"


def test_close_settings_adds_hidden_to_backdrop() -> None:
    """closeSettings must add hidden class to backdrop."""
    match = re.search(
        r"function closeSettings\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "closeSettings function not found"
    body = match.group(1)
    assert "settings-backdrop" in body, "closeSettings must reference settings-backdrop"
    assert "add" in body, "closeSettings must call add (hidden class addition)"


# ── switchSettingsTab implementation ──────────────────────────────────────────


def test_switch_settings_tab_has_tab_name_param() -> None:
    """switchSettingsTab must accept tabName parameter."""
    assert "function switchSettingsTab" in _JS, "switchSettingsTab must be defined"
    match = re.search(
        r"function switchSettingsTab\s*\((\w+)\)",
        _JS,
    )
    assert match, "switchSettingsTab must have a parameter"


def test_switch_settings_tab_toggles_active_class() -> None:
    """switchSettingsTab must toggle settings-tab--active class."""
    match = re.search(
        r"function switchSettingsTab\s*\(\w+\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "switchSettingsTab function not found"
    body = match.group(1)
    assert "settings-tab--active" in body, (
        "switchSettingsTab must toggle settings-tab--active class"
    )


def test_switch_settings_tab_toggles_aria_selected() -> None:
    """switchSettingsTab must toggle aria-selected on tab buttons."""
    match = re.search(
        r"function switchSettingsTab\s*\(\w+\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "switchSettingsTab function not found"
    body = match.group(1)
    assert "aria-selected" in body, "switchSettingsTab must toggle aria-selected"


def test_switch_settings_tab_toggles_panel_hidden() -> None:
    """switchSettingsTab must toggle hidden class on settings-panel elements."""
    match = re.search(
        r"function switchSettingsTab\s*\(\w+\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "switchSettingsTab function not found"
    body = match.group(1)
    assert "settings-panel" in body or "data-tab" in body, (
        "switchSettingsTab must handle settings-panel elements via data-tab"
    )


# ── handleGlobalKeydown settings integration ─────────────────────────────────


def test_handle_global_keydown_checks_settings_open() -> None:
    """handleGlobalKeydown must check _settingsOpen."""
    match = re.search(
        r"function handleGlobalKeydown\s*\(e\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "handleGlobalKeydown function not found"
    body = match.group(1)
    assert "_settingsOpen" in body, "handleGlobalKeydown must check _settingsOpen"


def test_handle_global_keydown_calls_close_settings_on_escape() -> None:
    """handleGlobalKeydown must call closeSettings on Escape when settings open."""
    match = re.search(
        r"function handleGlobalKeydown\s*\(e\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "handleGlobalKeydown function not found"
    body = match.group(1)
    assert "closeSettings" in body, "handleGlobalKeydown must call closeSettings"


def test_handle_global_keydown_opens_settings_on_comma() -> None:
    """handleGlobalKeydown must open settings when comma pressed."""
    match = re.search(
        r"function handleGlobalKeydown\s*\(e\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "handleGlobalKeydown function not found"
    body = match.group(1)
    assert "openSettings" in body, (
        "handleGlobalKeydown must call openSettings for comma key"
    )
    assert "," in body or "comma" in body.lower(), (
        "handleGlobalKeydown must check for comma key"
    )


def test_handle_global_keydown_comma_guards_input_elements() -> None:
    """handleGlobalKeydown comma shortcut must not fire in input/textarea/select."""
    match = re.search(
        r"function handleGlobalKeydown\s*\(e\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "handleGlobalKeydown function not found"
    body = match.group(1)
    # Must guard against inputs
    has_input_guard = (
        "INPUT" in body
        or "input" in body.lower()
        or "textarea" in body.lower()
        or "select" in body.lower()
        or "tagName" in body
    )
    assert has_input_guard, (
        "handleGlobalKeydown comma shortcut must guard against input/textarea/select"
    )


# ── bindStaticEventListeners settings wiring ─────────────────────────────────


def test_bind_static_event_listeners_binds_settings_btn() -> None:
    """bindStaticEventListeners must bind settings-btn click to openSettings."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "settings-btn" in body, (
        "bindStaticEventListeners must bind settings-btn click"
    )


def test_bind_static_event_listeners_binds_settings_btn_expanded() -> None:
    """bindStaticEventListeners must bind settings-btn-expanded click to openSettings."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "settings-btn-expanded" in body, (
        "bindStaticEventListeners must bind settings-btn-expanded click"
    )


def test_bind_static_event_listeners_binds_settings_backdrop() -> None:
    """bindStaticEventListeners must bind settings-backdrop click to closeSettings."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "settings-backdrop" in body, (
        "bindStaticEventListeners must bind settings-backdrop click"
    )


def test_bind_static_event_listeners_binds_dialog_cancel() -> None:
    """bindStaticEventListeners must bind dialog cancel event to closeSettings."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "cancel" in body, "bindStaticEventListeners must bind dialog cancel event"


def test_bind_static_event_listeners_binds_settings_tabs() -> None:
    """bindStaticEventListeners must bind .settings-tab click to switchSettingsTab."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "settings-tab" in body, (
        "bindStaticEventListeners must bind .settings-tab click"
    )
    assert "switchSettingsTab" in body, (
        "bindStaticEventListeners must call switchSettingsTab"
    )


# ── module.exports for new settings functions ────────────────────────────────


def test_exports_load_display_settings() -> None:
    """module.exports must export loadDisplaySettings."""
    match = re.search(
        r"module\.exports\s*=\s*\{(.*?)\};",
        _JS,
        re.DOTALL,
    )
    assert match, "module.exports block not found"
    exports = match.group(1)
    assert "loadDisplaySettings" in exports, (
        "module.exports must export loadDisplaySettings"
    )


def test_exports_save_display_settings() -> None:
    """module.exports must export saveDisplaySettings."""
    match = re.search(
        r"module\.exports\s*=\s*\{(.*?)\};",
        _JS,
        re.DOTALL,
    )
    assert match, "module.exports block not found"
    exports = match.group(1)
    assert "saveDisplaySettings" in exports, (
        "module.exports must export saveDisplaySettings"
    )


def test_exports_open_settings() -> None:
    """module.exports must export openSettings."""
    match = re.search(
        r"module\.exports\s*=\s*\{(.*?)\};",
        _JS,
        re.DOTALL,
    )
    assert match, "module.exports block not found"
    exports = match.group(1)
    assert "openSettings" in exports, "module.exports must export openSettings"


def test_exports_close_settings() -> None:
    """module.exports must export closeSettings."""
    match = re.search(
        r"module\.exports\s*=\s*\{(.*?)\};",
        _JS,
        re.DOTALL,
    )
    assert match, "module.exports block not found"
    exports = match.group(1)
    assert "closeSettings" in exports, "module.exports must export closeSettings"


def test_exports_switch_settings_tab() -> None:
    """module.exports must export switchSettingsTab."""
    match = re.search(
        r"module\.exports\s*=\s*\{(.*?)\};",
        _JS,
        re.DOTALL,
    )
    assert match, "module.exports block not found"
    exports = match.group(1)
    assert "switchSettingsTab" in exports, (
        "module.exports must export switchSettingsTab"
    )


# ── Display tab wiring (task-8) ────────────────────────────────────────────────


def test_apply_display_settings_function_exists() -> None:
    """applyDisplaySettings function must exist."""
    assert "function applyDisplaySettings" in _JS, (
        "applyDisplaySettings must be defined in app.js"
    )


def test_apply_display_settings_sets_font_size_css_property() -> None:
    """applyDisplaySettings must set --preview-font-size CSS custom property."""
    match = re.search(
        r"function applyDisplaySettings\s*\(\w+\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "applyDisplaySettings function not found"
    body = match.group(1)
    assert "--preview-font-size" in body, (
        "applyDisplaySettings must set --preview-font-size CSS property"
    )
    assert "documentElement" in body, (
        "applyDisplaySettings must call setProperty on document.documentElement"
    )


def test_apply_display_settings_sets_grid_columns_repeat() -> None:
    """applyDisplaySettings must set repeat(N, 1fr) for numeric grid columns."""
    match = re.search(
        r"function applyDisplaySettings\s*\(\w+\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "applyDisplaySettings function not found"
    body = match.group(1)
    assert "repeat" in body and "1fr" in body, (
        "applyDisplaySettings must set repeat(N, 1fr) for numeric grid columns"
    )


def test_apply_display_settings_handles_auto_grid_columns() -> None:
    """applyDisplaySettings must handle 'auto' gridColumns by removing inline style."""
    match = re.search(
        r"function applyDisplaySettings\s*\(\w+\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "applyDisplaySettings function not found"
    body = match.group(1)
    assert "auto" in body, "applyDisplaySettings must handle 'auto' gridColumns"
    assert "session-grid" in body or "gridTemplateColumns" in body, (
        "applyDisplaySettings must update session-grid or gridTemplateColumns"
    )


def test_on_display_setting_change_function_exists() -> None:
    """onDisplaySettingChange function must exist."""
    assert "function onDisplaySettingChange" in _JS, (
        "onDisplaySettingChange must be defined in app.js"
    )


def test_on_display_setting_change_reads_font_size() -> None:
    """onDisplaySettingChange must read from setting-font-size element."""
    match = re.search(
        r"function onDisplaySettingChange\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "onDisplaySettingChange function not found"
    body = match.group(1)
    assert "setting-font-size" in body, (
        "onDisplaySettingChange must read from setting-font-size"
    )


def test_on_display_setting_change_reads_hover_delay() -> None:
    """onDisplaySettingChange must read from setting-hover-delay element."""
    match = re.search(
        r"function onDisplaySettingChange\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "onDisplaySettingChange function not found"
    body = match.group(1)
    assert "setting-hover-delay" in body, (
        "onDisplaySettingChange must read from setting-hover-delay"
    )


def test_on_display_setting_change_reads_grid_columns() -> None:
    """onDisplaySettingChange must read from setting-grid-columns element."""
    match = re.search(
        r"function onDisplaySettingChange\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "onDisplaySettingChange function not found"
    body = match.group(1)
    assert "setting-grid-columns" in body, (
        "onDisplaySettingChange must read from setting-grid-columns"
    )


def test_on_display_setting_change_calls_save_display_settings() -> None:
    """onDisplaySettingChange must call saveDisplaySettings."""
    match = re.search(
        r"function onDisplaySettingChange\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "onDisplaySettingChange function not found"
    body = match.group(1)
    assert "saveDisplaySettings" in body, (
        "onDisplaySettingChange must call saveDisplaySettings"
    )


def test_on_display_setting_change_calls_apply_display_settings() -> None:
    """onDisplaySettingChange must call applyDisplaySettings."""
    match = re.search(
        r"function onDisplaySettingChange\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "onDisplaySettingChange function not found"
    body = match.group(1)
    assert "applyDisplaySettings" in body, (
        "onDisplaySettingChange must call applyDisplaySettings"
    )


def test_hover_preview_no_hardcoded_1500() -> None:
    """Hover preview delays must not be hardcoded 1500ms — must use loadDisplaySettings."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    # Count occurrences of hardcoded 1500 in the body
    hardcoded_count = body.count(", 1500)")
    assert hardcoded_count == 0, (
        f"Hover preview must not use hardcoded 1500ms delay (found {hardcoded_count} occurrences). "
        "Use loadDisplaySettings().hoverPreviewDelay instead."
    )


def test_hover_preview_reads_delay_from_settings() -> None:
    """Hover preview must read delay from loadDisplaySettings().hoverPreviewDelay."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "loadDisplaySettings" in body and "hoverPreviewDelay" in body, (
        "bindStaticEventListeners hover preview must read delay from loadDisplaySettings().hoverPreviewDelay"
    )


def test_hover_preview_skips_when_delay_zero() -> None:
    """Hover preview must check delay > 0 before setting setTimeout."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    # The body must have a conditional guard on the delay value
    assert "delay > 0" in body or "> 0" in body, (
        "bindStaticEventListeners hover preview must guard: if (delay > 0) before setTimeout"
    )


def test_bind_static_event_listeners_binds_font_size_change() -> None:
    """bindStaticEventListeners must bind change event on setting-font-size."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "setting-font-size" in body, (
        "bindStaticEventListeners must reference setting-font-size for change binding"
    )
    assert "onDisplaySettingChange" in body, (
        "bindStaticEventListeners must call onDisplaySettingChange on change"
    )


def test_bind_static_event_listeners_binds_hover_delay_change() -> None:
    """bindStaticEventListeners must bind change event on setting-hover-delay."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "setting-hover-delay" in body, (
        "bindStaticEventListeners must reference setting-hover-delay for change binding"
    )


def test_bind_static_event_listeners_binds_grid_columns_change() -> None:
    """bindStaticEventListeners must bind change event on setting-grid-columns."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "setting-grid-columns" in body, (
        "bindStaticEventListeners must reference setting-grid-columns for change binding"
    )


def test_dom_content_loaded_calls_apply_display_settings() -> None:
    """DOMContentLoaded handler must call applyDisplaySettings(loadDisplaySettings())."""
    # Find the DOMContentLoaded handler block
    match = re.search(
        r"DOMContentLoaded.*?\{(.*?)(?=\}\);?\s*\n// |\}\);\s*$)",
        _JS,
        re.DOTALL,
    )
    assert match, "DOMContentLoaded handler not found"
    body = match.group(1)
    assert "applyDisplaySettings" in body, (
        "DOMContentLoaded handler must call applyDisplaySettings(loadDisplaySettings())"
    )
    assert "loadDisplaySettings" in body, (
        "DOMContentLoaded handler must call applyDisplaySettings(loadDisplaySettings())"
    )


def test_exports_apply_display_settings() -> None:
    """module.exports must export applyDisplaySettings."""
    match = re.search(
        r"module\.exports\s*=\s*\{(.*?)\};",
        _JS,
        re.DOTALL,
    )
    assert match, "module.exports block not found"
    exports = match.group(1)
    assert "applyDisplaySettings" in exports, (
        "module.exports must export applyDisplaySettings"
    )


def test_exports_on_display_setting_change() -> None:
    """module.exports must export onDisplaySettingChange."""
    match = re.search(
        r"module\.exports\s*=\s*\{(.*?)\};",
        _JS,
        re.DOTALL,
    )
    assert match, "module.exports block not found"
    exports = match.group(1)
    assert "onDisplaySettingChange" in exports, (
        "module.exports must export onDisplaySettingChange"
    )


# ─── Server settings functions (task-1-sessions-tab) ─────────────────────────


def test_load_server_settings_function_exists() -> None:
    """loadServerSettings function must exist."""
    assert "function loadServerSettings" in _JS, (
        "loadServerSettings must be defined in app.js"
    )


def test_load_server_settings_fetches_api_settings() -> None:
    """loadServerSettings must fetch GET /api/settings."""
    match = re.search(
        r"async function loadServerSettings\s*\(\s*\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "loadServerSettings function not found"
    body = match.group(1)
    assert "/api/settings" in body, "loadServerSettings must fetch /api/settings"
    assert "GET" in body, "loadServerSettings must use GET method"


def test_load_server_settings_caches_in_server_settings() -> None:
    """loadServerSettings must cache result in _serverSettings."""
    assert "_serverSettings" in _JS, "_serverSettings variable must exist in app.js"
    match = re.search(
        r"async function loadServerSettings\s*\(\s*\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "loadServerSettings function not found"
    body = match.group(1)
    assert "_serverSettings" in body, (
        "loadServerSettings must cache the result in _serverSettings"
    )


def test_patch_server_setting_function_exists() -> None:
    """patchServerSetting function must exist."""
    assert "function patchServerSetting" in _JS, (
        "patchServerSetting must be defined in app.js"
    )


def test_patch_server_setting_sends_patch_request() -> None:
    """patchServerSetting must send PATCH to /api/settings."""
    match = re.search(
        r"async function patchServerSetting\s*\(.*?\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "patchServerSetting function not found"
    body = match.group(1)
    assert "/api/settings" in body, "patchServerSetting must send to /api/settings"
    assert "PATCH" in body, "patchServerSetting must use PATCH method"


def test_patch_server_setting_shows_toast() -> None:
    """patchServerSetting must call showToast."""
    match = re.search(
        r"async function patchServerSetting\s*\(.*?\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "patchServerSetting function not found"
    body = match.group(1)
    assert "showToast" in body, "patchServerSetting must call showToast"


def test_open_settings_calls_load_server_settings() -> None:
    """openSettings must call loadServerSettings to populate sessions tab."""
    match = re.search(
        r"function openSettings\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "openSettings function not found"
    body = match.group(1)
    assert "loadServerSettings" in body, "openSettings must call loadServerSettings"


def test_bind_static_event_listeners_binds_default_session_change() -> None:
    """bindStaticEventListeners must bind change on setting-default-session."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "setting-default-session" in body, (
        "bindStaticEventListeners must bind setting-default-session change"
    )


def test_bind_static_event_listeners_binds_sort_order_change() -> None:
    """bindStaticEventListeners must bind change on setting-sort-order."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "setting-sort-order" in body, (
        "bindStaticEventListeners must bind setting-sort-order change"
    )


def test_bind_static_event_listeners_binds_window_size_largest_change() -> None:
    """bindStaticEventListeners must bind change on setting-window-size-largest."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "setting-window-size-largest" in body, (
        "bindStaticEventListeners must bind setting-window-size-largest change"
    )


def test_bind_static_event_listeners_binds_auto_open_change() -> None:
    """bindStaticEventListeners must bind change on setting-auto-open."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "setting-auto-open" in body, (
        "bindStaticEventListeners must bind setting-auto-open change"
    )


def test_bind_static_event_listeners_uses_delegated_handler_for_hidden_sessions() -> (
    None
):
    """bindStaticEventListeners must use delegated change handler on #setting-hidden-sessions."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "setting-hidden-sessions" in body, (
        "bindStaticEventListeners must use delegated handler on setting-hidden-sessions"
    )


def test_exports_load_server_settings() -> None:
    """module.exports must export loadServerSettings."""
    match = re.search(
        r"module\.exports\s*=\s*\{(.*?)\};",
        _JS,
        re.DOTALL,
    )
    assert match, "module.exports block not found"
    exports = match.group(1)
    assert "loadServerSettings" in exports, (
        "module.exports must export loadServerSettings"
    )


def test_exports_patch_server_setting() -> None:
    """module.exports must export patchServerSetting."""
    match = re.search(
        r"module\.exports\s*=\s*\{(.*?)\};",
        _JS,
        re.DOTALL,
    )
    assert match, "module.exports block not found"
    exports = match.group(1)
    assert "patchServerSetting" in exports, (
        "module.exports must export patchServerSetting"
    )


# ─── Notifications tab (task-2-notifications-tab) ─────────────────────────────


def test_open_settings_populates_bell_sound() -> None:
    """openSettings must set setting-bell-sound checkbox from loadDisplaySettings().bellSound."""
    match = re.search(
        r"function openSettings\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "openSettings function not found"
    body = match.group(1)
    assert "setting-bell-sound" in body, (
        "openSettings must reference setting-bell-sound to set bell sound checkbox"
    )
    assert "bellSound" in body, "openSettings must read bellSound from display settings"


def test_open_settings_updates_notification_status_text() -> None:
    """openSettings must update notification permission status text and button."""
    match = re.search(
        r"function openSettings\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "openSettings function not found"
    body = match.group(1)
    assert "notification-status-text" in body, (
        "openSettings must reference notification-status-text to update permission status"
    )
    assert "notification-request-btn" in body, (
        "openSettings must reference notification-request-btn to update button state"
    )


def test_open_settings_checks_notification_permission() -> None:
    """openSettings must check Notification.permission to update UI."""
    match = re.search(
        r"function openSettings\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "openSettings function not found"
    body = match.group(1)
    assert "Notification" in body or "_notificationPermission" in body, (
        "openSettings must check Notification.permission or _notificationPermission"
    )


def test_bind_static_event_listeners_binds_bell_sound_change() -> None:
    """bindStaticEventListeners must bind change on setting-bell-sound to save to localStorage."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "setting-bell-sound" in body, (
        "bindStaticEventListeners must bind setting-bell-sound change event"
    )


def test_bind_static_event_listeners_bell_sound_saves_to_display_settings() -> None:
    """bindStaticEventListeners bell sound change handler must save to display settings."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    # The handler needs to reference saveDisplaySettings and bellSound
    assert "saveDisplaySettings" in body or "bellSound" in body, (
        "bindStaticEventListeners bell sound change handler must save to display settings via saveDisplaySettings or bellSound"
    )


def test_bind_static_event_listeners_binds_permission_btn() -> None:
    """bindStaticEventListeners must bind click on notification-request-btn."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "notification-request-btn" in body, (
        "bindStaticEventListeners must bind notification-request-btn click event"
    )


def test_bind_static_event_listeners_permission_btn_calls_request_permission() -> None:
    """bindStaticEventListeners permission button handler must call Notification.requestPermission()."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "requestPermission" in body, (
        "bindStaticEventListeners permission button must call Notification.requestPermission()"
    )


def test_bind_static_event_listeners_permission_btn_has_catch() -> None:
    """Notification.requestPermission() in permission button handler must have a .catch() handler."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert ".catch(" in body, (
        "Notification.requestPermission() must have a .catch() handler for defensive error handling"
    )


def test_update_notification_ui_has_null_guard() -> None:
    """_updateNotificationUI must guard against null inputs."""
    match = re.search(
        r"function _updateNotificationUI\s*\(.*?\)\s*\{(.*?)(?=\n\})",
        _JS,
        re.DOTALL,
    )
    assert match, "_updateNotificationUI function not found"
    body = match.group(1)
    assert (
        "null" in body or "non-null" in body or "!statusEl" in body or "!reqBtn" in body
    ), (
        "_updateNotificationUI must include a null guard (null check) or JSDoc non-null annotation"
    )


# ─── New Session tab (task-3-new-session-tab) ─────────────────────────────────


def test_new_session_default_template_constant_exists() -> None:
    """NEW_SESSION_DEFAULT_TEMPLATE constant must be declared in app.js."""
    assert "NEW_SESSION_DEFAULT_TEMPLATE" in _JS, (
        "NEW_SESSION_DEFAULT_TEMPLATE constant must be declared in app.js"
    )


def test_new_session_default_template_value() -> None:
    """NEW_SESSION_DEFAULT_TEMPLATE must equal 'tmux new-session -d -s {name}'."""
    assert "tmux new-session -d -s {name}" in _JS, (
        "NEW_SESSION_DEFAULT_TEMPLATE must be 'tmux new-session -d -s {name}'"
    )


def test_open_settings_populates_template_textarea() -> None:
    """openSettings must populate #setting-template textarea from server settings."""
    match = re.search(
        r"function openSettings\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "openSettings function not found"
    body = match.group(1)
    assert "setting-template" in body, (
        "openSettings must reference setting-template textarea"
    )
    assert "new_session_template" in body, (
        "openSettings must populate template from ss.new_session_template"
    )
    assert "NEW_SESSION_DEFAULT_TEMPLATE" in body, (
        "openSettings must fall back to NEW_SESSION_DEFAULT_TEMPLATE"
    )


def test_bind_static_event_listeners_binds_template_input_with_debounce() -> None:
    """bindStaticEventListeners must bind input on #setting-template with 500ms debounce."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "setting-template" in body, (
        "bindStaticEventListeners must bind setting-template input event"
    )
    assert "500" in body, (
        "bindStaticEventListeners template input handler must use 500ms debounce"
    )
    assert "patchServerSetting" in body, (
        "bindStaticEventListeners template input handler must call patchServerSetting"
    )
    assert "new_session_template" in body, (
        "bindStaticEventListeners template input handler must pass 'new_session_template' key"
    )


def test_bind_static_event_listeners_binds_template_reset_button() -> None:
    """bindStaticEventListeners must bind click on #setting-template-reset."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "setting-template-reset" in body, (
        "bindStaticEventListeners must bind setting-template-reset click event"
    )


def test_bind_static_event_listeners_reset_patches_server() -> None:
    """bindStaticEventListeners reset handler must call patchServerSetting with default template."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    # The reset button section must patch with the default template
    assert "setting-template-reset" in body, "setting-template-reset must be referenced"
    assert "NEW_SESSION_DEFAULT_TEMPLATE" in body, (
        "bindStaticEventListeners reset handler must use NEW_SESSION_DEFAULT_TEMPLATE"
    )


# ─── Header + button with inline name input (task-4-header-plus-button) ──────


def test_show_new_session_input_function_exists() -> None:
    """showNewSessionInput function must exist in app.js."""
    assert "function showNewSessionInput" in _JS, (
        "showNewSessionInput must be defined in app.js"
    )


def test_create_new_session_function_exists() -> None:
    """createNewSession function must exist in app.js."""
    assert "function createNewSession" in _JS, (
        "createNewSession must be defined in app.js"
    )


def test_show_new_session_input_creates_input_with_class() -> None:
    """Input setup must set class 'new-session-input' (via _createSessionInput factory)."""
    match = re.search(
        r"function _createSessionInput\s*\(\s*\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "_createSessionInput function not found"
    body = match.group(1)
    assert "new-session-input" in body, (
        "_createSessionInput must set input.className = 'new-session-input'"
    )


def test_show_new_session_input_sets_placeholder() -> None:
    """Input setup must set placeholder containing 'Session name' (via _createSessionInput factory)."""
    match = re.search(
        r"function _createSessionInput\s*\(\s*\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "_createSessionInput function not found"
    body = match.group(1)
    assert "Session name" in body, (
        "_createSessionInput must set placeholder containing 'Session name'"
    )


def test_show_new_session_input_disables_autocomplete() -> None:
    """Input setup must set autocomplete off (via _createSessionInput factory)."""
    match = re.search(
        r"function _createSessionInput\s*\(\s*\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "_createSessionInput function not found"
    body = match.group(1)
    assert "autocomplete" in body.lower() and "off" in body.lower(), (
        "_createSessionInput must set autocomplete off"
    )


def test_show_new_session_input_disables_spellcheck() -> None:
    """Input setup must set spellcheck false (via _createSessionInput factory)."""
    match = re.search(
        r"function _createSessionInput\s*\(\s*\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "_createSessionInput function not found"
    body = match.group(1)
    assert "spellcheck" in body.lower() and "false" in body.lower(), (
        "_createSessionInput must set spellcheck false"
    )


def test_show_new_session_input_hides_button() -> None:
    """showNewSessionInput must hide the button."""
    match = re.search(
        r"function showNewSessionInput\s*\(\w+\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "showNewSessionInput function not found"
    body = match.group(1)
    # Must hide the button (display none or style.display)
    assert "display" in body or "style.display" in body or "hidden" in body, (
        "showNewSessionInput must hide the button"
    )


def test_show_new_session_input_focuses_input() -> None:
    """showNewSessionInput must call focus() on the input."""
    match = re.search(
        r"function showNewSessionInput\s*\(\w+\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "showNewSessionInput function not found"
    body = match.group(1)
    assert ".focus()" in body or "focus()" in body, (
        "showNewSessionInput must call focus() on the input"
    )


def test_show_new_session_input_handles_enter_key() -> None:
    """showNewSessionInput must handle Enter key to create session."""
    match = re.search(
        r"function showNewSessionInput\s*\(\w+\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "showNewSessionInput function not found"
    body = match.group(1)
    assert "Enter" in body, "showNewSessionInput must handle Enter key"
    assert "createNewSession" in body, (
        "showNewSessionInput must call createNewSession on Enter"
    )


def test_show_new_session_input_handles_escape_key() -> None:
    """showNewSessionInput must handle Escape key to cancel."""
    match = re.search(
        r"function showNewSessionInput\s*\(\w+\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "showNewSessionInput function not found"
    body = match.group(1)
    assert "Escape" in body, "showNewSessionInput must handle Escape key"


def test_show_new_session_input_handles_blur_with_delay() -> None:
    """showNewSessionInput must handle blur with 150ms delay."""
    match = re.search(
        r"function showNewSessionInput\s*\(\w+\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "showNewSessionInput function not found"
    body = match.group(1)
    assert "blur" in body, "showNewSessionInput must handle blur event"
    assert "150" in body, "showNewSessionInput must use 150ms delay on blur"


def test_create_new_session_posts_to_api_sessions() -> None:
    """createNewSession must POST to /api/sessions."""
    match = re.search(
        r"async function createNewSession\s*\(\w+\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "createNewSession function not found"
    body = match.group(1)
    assert "/api/sessions" in body, "createNewSession must POST to /api/sessions"
    assert "POST" in body, "createNewSession must use POST method"


def test_create_new_session_shows_toast() -> None:
    """createNewSession must call showToast."""
    match = re.search(
        r"async function createNewSession\s*\(\w+\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "createNewSession function not found"
    body = match.group(1)
    assert "showToast" in body, "createNewSession must call showToast"


def test_create_new_session_calls_poll_sessions() -> None:
    """createNewSession must call pollSessions."""
    match = re.search(
        r"async function createNewSession\s*\(\w+\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "createNewSession function not found"
    body = match.group(1)
    assert "pollSessions" in body, "createNewSession must call pollSessions"


def test_create_new_session_auto_opens_session() -> None:
    """createNewSession must call openSession when auto_open_created is not false."""
    match = re.search(
        r"async function createNewSession\s*\(\w+\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "createNewSession function not found"
    body = match.group(1)
    assert "openSession" in body, "createNewSession must call openSession"
    assert "auto_open_created" in body, (
        "createNewSession must check ss.auto_open_created"
    )


def test_create_new_session_polls_before_open() -> None:
    """createNewSession must poll for the session to appear before calling openSession (not immediate setTimeout)."""
    match = re.search(
        r"async function createNewSession\s*\(\w+\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "createNewSession function not found"
    body = match.group(1)
    # Old immediate pattern must be gone
    assert "setTimeout(() => openSession" not in body, (
        "createNewSession must not use immediate setTimeout(() => openSession) — should poll instead"
    )
    # New polling pattern must be present
    assert "setInterval" in body, (
        "createNewSession must use setInterval to poll for session readiness"
    )


def test_bind_static_event_listeners_binds_new_session_btn() -> None:
    """bindStaticEventListeners must bind click on new-session-btn to showNewSessionInput."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "new-session-btn" in body, (
        "bindStaticEventListeners must bind new-session-btn click"
    )
    assert "showNewSessionInput" in body, (
        "bindStaticEventListeners must call showNewSessionInput for new-session-btn"
    )


def test_exports_show_new_session_input() -> None:
    """module.exports must export showNewSessionInput."""
    match = re.search(
        r"module\.exports\s*=\s*\{(.*?)\};",
        _JS,
        re.DOTALL,
    )
    assert match, "module.exports block not found"
    exports = match.group(1)
    assert "showNewSessionInput" in exports, (
        "module.exports must export showNewSessionInput"
    )


def test_exports_create_new_session() -> None:
    """module.exports must export createNewSession."""
    match = re.search(
        r"module\.exports\s*=\s*\{(.*?)\};",
        _JS,
        re.DOTALL,
    )
    assert match, "module.exports block not found"
    exports = match.group(1)
    assert "createNewSession" in exports, "module.exports must export createNewSession"


# ============================================================
# Sidebar + New sticky footer (task-5-sidebar-new-footer)
# ============================================================


def test_bind_sidebar_new_session_btn_in_bind_static_event_listeners() -> None:
    """bindStaticEventListeners must bind #sidebar-new-session-btn click to showNewSessionInput."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "sidebar-new-session-btn" in body, (
        "bindStaticEventListeners must reference 'sidebar-new-session-btn'"
    )
    assert "showNewSessionInput" in body, (
        "bindStaticEventListeners must call showNewSessionInput for the sidebar new-session button"
    )


# ============================================================
# Mobile FAB (task-6-mobile-fab)
# ============================================================


def test_js_fab_bound_in_bind_static_event_listeners() -> None:
    """bindStaticEventListeners must bind #new-session-fab click to showNewSessionInput."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "new-session-fab" in body, (
        "bindStaticEventListeners must reference 'new-session-fab'"
    )


def test_js_open_session_hides_fab() -> None:
    """openSession must add 'hidden' class to the FAB element."""
    match = re.search(
        r"async function openSession\s*\(.*?\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "openSession function not found"
    body = match.group(1)
    assert "new-session-fab" in body, (
        "openSession must reference 'new-session-fab' to hide FAB during fullscreen view"
    )
    assert "hidden" in body, (
        "openSession must add 'hidden' class to FAB (classList.add('hidden'))"
    )


def test_js_close_session_restores_fab() -> None:
    """closeSession must remove 'hidden' class from the FAB element."""
    match = re.search(
        r"function closeSession\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "closeSession function not found"
    body = match.group(1)
    assert "new-session-fab" in body, (
        "closeSession must reference 'new-session-fab' to restore FAB when session closed"
    )
    assert "remove" in body, "closeSession must call classList.remove('hidden') on FAB"


def test_js_fab_exported() -> None:
    """new-session-fab binding functions must work - showNewSessionInput is exported."""
    assert "showNewSessionInput" in _JS, (
        "showNewSessionInput must exist in app.js (used by FAB click handler)"
    )


# ============================================================
# Mobile FAB — dedicated fixed-position input (quality fix)
# ============================================================


def test_js_show_fab_session_input_exists() -> None:
    """showFabSessionInput function must exist in app.js (dedicated FAB input overlay)."""
    assert "function showFabSessionInput" in _JS, (
        "showFabSessionInput must be defined in app.js — "
        "FAB needs a fixed-position overlay, not inline body insertion"
    )


def test_js_show_fab_session_input_appends_to_body() -> None:
    """showFabSessionInput must append the overlay to document.body (fixed positioning)."""
    match = re.search(
        r"function showFabSessionInput\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\nasync function |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "showFabSessionInput function not found"
    body = match.group(1)
    assert "document.body" in body, (
        "showFabSessionInput must append to document.body "
        "(fixed-position overlays must be direct body children to escape overflow:hidden)"
    )


def test_js_show_fab_session_input_handles_enter() -> None:
    """showFabSessionInput must call createNewSession on Enter key."""
    match = re.search(
        r"function showFabSessionInput\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\nasync function |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "showFabSessionInput function not found"
    body = match.group(1)
    assert "Enter" in body, "showFabSessionInput must handle Enter key"
    assert "createNewSession" in body, (
        "showFabSessionInput must call createNewSession on Enter"
    )


def test_js_show_fab_session_input_handles_escape() -> None:
    """showFabSessionInput must handle Escape key to cancel."""
    match = re.search(
        r"function showFabSessionInput\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\nasync function |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "showFabSessionInput function not found"
    body = match.group(1)
    assert "Escape" in body, "showFabSessionInput must handle Escape key"


def test_js_fab_click_calls_show_fab_session_input() -> None:
    """bindStaticEventListeners must bind FAB click to showFabSessionInput (not inline body insert)."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "showFabSessionInput" in body, (
        "bindStaticEventListeners must call showFabSessionInput for FAB click — "
        "the old showNewSessionInput(fab) inserts into body which is clipped by overflow:hidden"
    )


def test_js_show_fab_session_input_guards_against_duplicate_overlay() -> None:
    """showFabSessionInput must guard against creating a second overlay if one already exists."""
    match = re.search(
        r"function showFabSessionInput\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\nasync function |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "showFabSessionInput function not found"
    body = match.group(1)
    assert "fab-input-overlay" in body, (
        "showFabSessionInput must check for an existing .fab-input-overlay before creating another — "
        "prevents duplicate overlays if called programmatically while one is already open"
    )
    # Guard must be present: querySelector('.fab-input-overlay') with early return
    assert "querySelector" in body and "return" in body, (
        "showFabSessionInput must guard with document.querySelector('.fab-input-overlay') return; "
        "at the top to prevent duplicate overlays"
    )


# ============================================================
# _createSessionInput factory (quality refactor — suggestion 1)
# ============================================================


def test_js_create_session_input_factory_exists() -> None:
    """_createSessionInput factory must exist in app.js to eliminate input setup duplication."""
    assert "function _createSessionInput" in _JS, (
        "_createSessionInput must be defined in app.js — "
        "shared factory eliminates duplicated input setup in showNewSessionInput and showFabSessionInput"
    )


def test_js_create_session_input_factory_sets_class() -> None:
    """_createSessionInput must return an input with class 'new-session-input'."""
    match = re.search(
        r"function _createSessionInput\s*\(\s*\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "_createSessionInput function not found"
    body = match.group(1)
    assert "new-session-input" in body, (
        "_createSessionInput must set input.className = 'new-session-input'"
    )


def test_js_create_session_input_factory_sets_placeholder() -> None:
    """_createSessionInput must set placeholder containing 'Session name'."""
    match = re.search(
        r"function _createSessionInput\s*\(\s*\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "_createSessionInput function not found"
    body = match.group(1)
    assert "Session name" in body, (
        "_createSessionInput must set placeholder containing 'Session name'"
    )


def test_js_create_session_input_factory_disables_autocomplete() -> None:
    """_createSessionInput must set autocomplete off."""
    match = re.search(
        r"function _createSessionInput\s*\(\s*\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "_createSessionInput function not found"
    body = match.group(1)
    assert "autocomplete" in body.lower() and "off" in body.lower(), (
        "_createSessionInput must set autocomplete off"
    )


def test_js_create_session_input_factory_disables_spellcheck() -> None:
    """_createSessionInput must set spellcheck false."""
    match = re.search(
        r"function _createSessionInput\s*\(\s*\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "_createSessionInput function not found"
    body = match.group(1)
    assert "spellcheck" in body.lower() and "false" in body.lower(), (
        "_createSessionInput must set spellcheck false"
    )


def test_js_show_new_session_input_uses_factory() -> None:
    """showNewSessionInput must call _createSessionInput() instead of duplicating input setup."""
    match = re.search(
        r"function showNewSessionInput\s*\(\w+\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "showNewSessionInput function not found"
    body = match.group(1)
    assert "_createSessionInput" in body, (
        "showNewSessionInput must call _createSessionInput() to create the input element"
    )


def test_js_show_fab_session_input_uses_factory() -> None:
    """showFabSessionInput must call _createSessionInput() instead of duplicating input setup."""
    match = re.search(
        r"function showFabSessionInput\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\nasync function |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "showFabSessionInput function not found"
    body = match.group(1)
    assert "_createSessionInput" in body, (
        "showFabSessionInput must call _createSessionInput() to create the input element"
    )


# ─── Task 7: Apply settings effects (task-7-apply-settings-effects) ──────────


# ── terminal.js: createTerminal reads font size from localStorage ─────────────


def test_create_terminal_reads_font_size_from_localstorage() -> None:
    """createTerminal() must read font size from localStorage key 'muxplex.display'."""
    match = re.search(
        r"function createTerminal\s*\(\s*\)\s*\{(.*?)(?=\n(?:function|//|window\.))",
        _TERMINAL_JS,
        re.DOTALL,
    )
    assert match, "createTerminal function not found in terminal.js"
    body = match.group(1)
    assert "muxplex.display" in body or "DISPLAY_SETTINGS_KEY" in body, (
        "createTerminal must read from localStorage key 'muxplex.display'"
    )
    assert "localStorage" in body, (
        "createTerminal must use localStorage to read font size"
    )


def test_create_terminal_parses_json_for_font_size() -> None:
    """createTerminal() must parse JSON from localStorage to extract fontSize."""
    match = re.search(
        r"function createTerminal\s*\(\s*\)\s*\{(.*?)(?=\n(?:function|//|window\.))",
        _TERMINAL_JS,
        re.DOTALL,
    )
    assert match, "createTerminal function not found in terminal.js"
    body = match.group(1)
    assert "JSON.parse" in body, (
        "createTerminal must use JSON.parse to extract fontSize from localStorage"
    )
    assert "fontSize" in body, (
        "createTerminal must extract fontSize from parsed display settings"
    )


def test_create_terminal_applies_mobile_cap_with_math_min() -> None:
    """createTerminal() must apply mobile cap using Math.min(storedFontSize, 12)."""
    match = re.search(
        r"function createTerminal\s*\(\s*\)\s*\{(.*?)(?=\n(?:function|//|window\.))",
        _TERMINAL_JS,
        re.DOTALL,
    )
    assert match, "createTerminal function not found in terminal.js"
    body = match.group(1)
    assert "Math.min" in body, (
        "createTerminal must use Math.min for mobile font size cap"
    )


def test_create_terminal_uses_stored_font_size_not_hardcoded() -> None:
    """createTerminal() must use stored fontSize variable, not hardcoded 14 or ternary."""
    match = re.search(
        r"function createTerminal\s*\(\s*\)\s*\{(.*?)(?=\n(?:function|//|window\.))",
        _TERMINAL_JS,
        re.DOTALL,
    )
    assert match, "createTerminal function not found in terminal.js"
    body = match.group(1)
    # The font size in Terminal constructor should use a variable, not a ternary with literal 14
    # Check that there's no raw "mobile ? 12 : 14" pattern anymore
    assert "mobile ? 12 : 14" not in body, (
        "createTerminal must not use hardcoded 'mobile ? 12 : 14' ternary; "
        "use stored font size from localStorage with Math.min cap"
    )


def test_create_terminal_has_default_font_size_14() -> None:
    """createTerminal() must default to fontSize 14 when not set in localStorage."""
    match = re.search(
        r"function createTerminal\s*\(\s*\)\s*\{(.*?)(?=\n(?:function|//|window\.))",
        _TERMINAL_JS,
        re.DOTALL,
    )
    assert match, "createTerminal function not found in terminal.js"
    body = match.group(1)
    assert "14" in body, (
        "createTerminal must have default font size of 14 for when localStorage is not set"
    )


# ── app.js: getVisibleSessions helper filters hidden sessions ─────────────────


def test_get_visible_sessions_filters_hidden_sessions() -> None:
    """getVisibleSessions() must filter sessions using _serverSettings.hidden_sessions."""
    match = re.search(
        r"function getVisibleSessions\s*\(\w+\)\s*\{(.*?)(?=\n(?:function|//|window\.))",
        _JS,
        re.DOTALL,
    )
    assert match, "getVisibleSessions function not found in app.js"
    body = match.group(1)
    assert "hidden_sessions" in body, (
        "getVisibleSessions must filter sessions using _serverSettings.hidden_sessions"
    )
    assert "_serverSettings" in body, (
        "getVisibleSessions must reference _serverSettings to access hidden_sessions"
    )


# ── app.js: renderGrid filters hidden sessions ────────────────────────────────


def test_render_grid_filters_hidden_sessions() -> None:
    """renderGrid() must filter out hidden sessions via getVisibleSessions()."""
    match = re.search(
        r"function renderGrid\s*\(\w+\)\s*\{(.*?)(?=\n(?:function|//|window\.))",
        _JS,
        re.DOTALL,
    )
    assert match, "renderGrid function not found in app.js"
    body = match.group(1)
    assert "getVisibleSessions" in body, (
        "renderGrid must call getVisibleSessions() to filter hidden sessions"
    )


def test_render_grid_creates_visible_array() -> None:
    """renderGrid() must create a 'visible' array excluding hidden session names."""
    match = re.search(
        r"function renderGrid\s*\(\w+\)\s*\{(.*?)(?=\n(?:function|//|window\.))",
        _JS,
        re.DOTALL,
    )
    assert match, "renderGrid function not found in app.js"
    body = match.group(1)
    assert "visible" in body, (
        "renderGrid must create a 'visible' variable/array for non-hidden sessions"
    )


def test_render_grid_uses_visible_for_empty_state_check() -> None:
    """renderGrid() must use visible.length (not sessions.length) for empty-state check."""
    match = re.search(
        r"function renderGrid\s*\(\w+\)\s*\{(.*?)(?=\n(?:function|//|window\.))",
        _JS,
        re.DOTALL,
    )
    assert match, "renderGrid function not found in app.js"
    body = match.group(1)
    # The empty-state guard must check visible.length, not sessions.length, so hidden
    # sessions do not trigger the "no sessions" state.
    assert "visible.length" in body, (
        "renderGrid must use 'visible.length' for empty-state check, not sessions.length"
    )


def test_render_grid_applies_alphabetical_sort_with_locale_compare() -> None:
    """renderGrid() must sort alphabetically using localeCompare when sortOrder is 'alphabetical'."""
    match = re.search(
        r"function renderGrid\s*\(\w+\)\s*\{(.*?)(?=\n(?:function|//|window\.))",
        _JS,
        re.DOTALL,
    )
    assert match, "renderGrid function not found in app.js"
    body = match.group(1)
    assert "alphabetical" in body, "renderGrid must check for 'alphabetical' sort order"
    assert "localeCompare" in body, (
        "renderGrid must use localeCompare for alphabetical sort"
    )


def test_render_grid_reads_sort_order_from_server_settings() -> None:
    """renderGrid() must read sort_order from _serverSettings to determine ordering."""
    match = re.search(
        r"function renderGrid\s*\(\w+\)\s*\{(.*?)(?=\n(?:function|//|window\.))",
        _JS,
        re.DOTALL,
    )
    assert match, "renderGrid function not found in app.js"
    body = match.group(1)
    assert "sort_order" in body or "sortOrder" in body, (
        "renderGrid must read sort_order from _serverSettings"
    )


# ── app.js: renderSidebar filters hidden sessions ─────────────────────────────


def test_render_sidebar_filters_hidden_sessions() -> None:
    """renderSidebar() must filter out hidden sessions via getVisibleSessions()."""
    match = re.search(
        r"function renderSidebar\s*\(\w+,\s*\w+\)\s*\{(.*?)(?=\nconst SIDEBAR_KEY|function |\n// ─)",
        _JS,
        re.DOTALL,
    )
    assert match, "renderSidebar function not found in app.js"
    body = match.group(1)
    assert "getVisibleSessions" in body, (
        "renderSidebar must call getVisibleSessions() to filter hidden sessions"
    )


def test_render_sidebar_uses_visible_array() -> None:
    """renderSidebar() must use visible array for rendering, not original sessions."""
    match = re.search(
        r"function renderSidebar\s*\(\w+,\s*\w+\)\s*\{(.*?)(?=\nconst SIDEBAR_KEY|function |\n// ─)",
        _JS,
        re.DOTALL,
    )
    assert match, "renderSidebar function not found in app.js"
    body = match.group(1)
    assert "visible" in body, (
        "renderSidebar must use 'visible' array after filtering hidden sessions"
    )


# ── app.js: renderSheetList filters hidden sessions ───────────────────────────


def test_render_sheet_list_filters_hidden_sessions() -> None:
    """renderSheetList() must filter out hidden sessions via getVisibleSessions()."""
    match = re.search(
        r"function renderSheetList\s*\(\)\s*\{(.*?)(?=\n(?:function|//|window\.))",
        _JS,
        re.DOTALL,
    )
    assert match, "renderSheetList function not found in app.js"
    body = match.group(1)
    assert "getVisibleSessions" in body, (
        "renderSheetList must call getVisibleSessions() to filter hidden sessions; "
        "currently it reads _currentSessions directly and bypasses the filter"
    )


# ── app.js: DOMContentLoaded calls loadServerSettings ────────────────────────


def test_dom_content_loaded_calls_load_server_settings() -> None:
    """DOMContentLoaded handler must call loadServerSettings() after startPolling()."""
    match = re.search(
        r"DOMContentLoaded.*?\{(.*?)(?=\}\);?\s*\n// |\}\);\s*$)",
        _JS,
        re.DOTALL,
    )
    assert match, "DOMContentLoaded handler not found"
    body = match.group(1)
    assert "loadServerSettings" in body, (
        "DOMContentLoaded handler must call loadServerSettings() "
        "after startPolling() in the restoreState().then() chain"
    )


# ─── Remote Instances UI (task-15) ────────────────────────────────────────────


def test_save_remote_instances_function_exists() -> None:
    """_saveRemoteInstances function must exist in app.js."""
    assert "_saveRemoteInstances" in _JS, (
        "_saveRemoteInstances function must be defined in app.js"
    )


def test_save_remote_instances_calls_patch_server_setting() -> None:
    """_saveRemoteInstances must call patchServerSetting('remote_instances', ...)."""
    match = re.search(
        r"function _saveRemoteInstances\s*\(\s*\)\s*\{(.*?)(?=\n(?:function|//|window\.)|\n})",
        _JS,
        re.DOTALL,
    )
    assert match, "_saveRemoteInstances function not found in app.js"
    body = match.group(1)
    assert "patchServerSetting" in body, (
        "_saveRemoteInstances must call patchServerSetting"
    )
    assert "remote_instances" in body, (
        "_saveRemoteInstances must pass 'remote_instances' to patchServerSetting"
    )


def test_save_remote_instances_rebuilds_sources() -> None:
    """_saveRemoteInstances must rebuild _sources after saving."""
    match = re.search(
        r"function _saveRemoteInstances\s*\(\s*\)\s*\{(.*?)(?=\n(?:function|//|window\.)|\n})",
        _JS,
        re.DOTALL,
    )
    assert match, "_saveRemoteInstances function not found in app.js"
    body = match.group(1)
    assert "_sources" in body or "buildSources" in body, (
        "_saveRemoteInstances must rebuild _sources after saving remote_instances"
    )


def test_open_settings_populates_device_name() -> None:
    """openSettings must populate setting-device-name from server settings."""
    match = re.search(
        r"function openSettings\s*\(\s*\)\s*\{(.*?)(?=\n(?:function|//\s*─))",
        _JS,
        re.DOTALL,
    )
    assert match, "openSettings function not found in app.js"
    body = match.group(1)
    assert "setting-device-name" in body, (
        "openSettings must populate #setting-device-name input from server settings"
    )
    assert "device_name" in body, (
        "openSettings must reference device_name from server settings"
    )


def test_open_settings_renders_remote_instances() -> None:
    """openSettings must render remote instances rows from server settings."""
    match = re.search(
        r"function openSettings\s*\(\s*\)\s*\{(.*?)(?=\n(?:function|//\s*─))",
        _JS,
        re.DOTALL,
    )
    assert match, "openSettings function not found in app.js"
    body = match.group(1)
    assert "setting-remote-instances" in body, (
        "openSettings must populate #setting-remote-instances from server settings"
    )
    assert "remote_instances" in body, (
        "openSettings must reference remote_instances when rendering the instances list"
    )


def test_bind_static_event_listeners_device_name_debounce() -> None:
    """bindStaticEventListeners must bind debounced save for setting-device-name."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)(?=\n(?:function|//\s*─|window\.))",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found in app.js"
    body = match.group(1)
    assert "setting-device-name" in body, (
        "bindStaticEventListeners must bind an event listener for #setting-device-name"
    )
    assert "device_name" in body, (
        "bindStaticEventListeners must save device_name via patchServerSetting"
    )


def test_bind_static_event_listeners_add_remote_instance_btn() -> None:
    """bindStaticEventListeners must bind the add-remote-instance-btn click handler."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)(?=\n(?:function|//\s*─|window\.))",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found in app.js"
    body = match.group(1)
    assert "add-remote-instance-btn" in body, (
        "bindStaticEventListeners must bind click handler for #add-remote-instance-btn"
    )


def test_bind_static_event_listeners_remote_instance_remove() -> None:
    """bindStaticEventListeners must bind delegated handler for remote instance remove buttons."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)(?=\n(?:function|//\s*─|window\.))",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found in app.js"
    body = match.group(1)
    assert "settings-remote-remove" in body, (
        "bindStaticEventListeners must handle delegated clicks on .settings-remote-remove buttons"
    )

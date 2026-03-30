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
    assert "notificationPermission" in _JS, "DISPLAY_DEFAULTS must include notificationPermission"


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
    assert "Object.assign" in body, "loadDisplaySettings must use Object.assign to merge with defaults"


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
    assert "setting-font-size" in body, "openSettings must set setting-font-size form control"
    assert "setting-hover-delay" in body, "openSettings must set setting-hover-delay form control"
    assert "setting-grid-columns" in body, "openSettings must set setting-grid-columns form control"


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
    assert "_settingsOpen" in body, (
        "handleGlobalKeydown must check _settingsOpen"
    )


def test_handle_global_keydown_calls_close_settings_on_escape() -> None:
    """handleGlobalKeydown must call closeSettings on Escape when settings open."""
    match = re.search(
        r"function handleGlobalKeydown\s*\(e\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "handleGlobalKeydown function not found"
    body = match.group(1)
    assert "closeSettings" in body, (
        "handleGlobalKeydown must call closeSettings"
    )


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
        "INPUT" in body or "input" in body.lower() or
        "textarea" in body.lower() or "select" in body.lower() or
        "tagName" in body
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
    assert "cancel" in body, (
        "bindStaticEventListeners must bind dialog cancel event"
    )


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
    assert "openSettings" in exports, (
        "module.exports must export openSettings"
    )


def test_exports_close_settings() -> None:
    """module.exports must export closeSettings."""
    match = re.search(
        r"module\.exports\s*=\s*\{(.*?)\};",
        _JS,
        re.DOTALL,
    )
    assert match, "module.exports block not found"
    exports = match.group(1)
    assert "closeSettings" in exports, (
        "module.exports must export closeSettings"
    )


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
    assert "auto" in body, (
        "applyDisplaySettings must handle 'auto' gridColumns"
    )
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
    assert "loadServerSettings" in body, (
        "openSettings must call loadServerSettings"
    )


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


def test_bind_static_event_listeners_uses_delegated_handler_for_hidden_sessions() -> None:
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
    assert "bellSound" in body, (
        "openSettings must read bellSound from display settings"
    )


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

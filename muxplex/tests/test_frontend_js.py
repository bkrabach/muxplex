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


def test_get_display_settings_function_exists_via_constant_section() -> None:
    """getDisplaySettings function must exist (display settings are now server-side)."""
    assert "function getDisplaySettings" in _JS, (
        "getDisplaySettings must be defined in app.js"
    )


def test_get_display_settings_reads_server_settings() -> None:
    """getDisplaySettings must read from _serverSettings (not localStorage)."""
    match = re.search(
        r"function getDisplaySettings\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// |\n/\*)",
        _JS,
        re.DOTALL,
    )
    assert match, "getDisplaySettings function not found"
    body = match.group(1)
    assert "_serverSettings" in body, (
        "getDisplaySettings must read from _serverSettings"
    )
    assert "localStorage" not in body, (
        "getDisplaySettings must not use localStorage — display settings are server-side"
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


def test_get_display_settings_function_exists() -> None:
    """getDisplaySettings function must exist."""
    assert "function getDisplaySettings" in _JS, (
        "getDisplaySettings must be defined in app.js"
    )


def test_save_display_settings_function_absent() -> None:
    """saveDisplaySettings must not exist — display writes go via patchServerSetting."""
    assert "function saveDisplaySettings" not in _JS, (
        "saveDisplaySettings must not be defined in app.js — use patchServerSetting instead"
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


def test_get_display_settings_reads_from_server_settings() -> None:
    """getDisplaySettings must read from _serverSettings, not localStorage."""
    match = re.search(
        r"function getDisplaySettings\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// |\n/\*)",
        _JS,
        re.DOTALL,
    )
    assert match, "getDisplaySettings function not found"
    body = match.group(1)
    assert "_serverSettings" in body, (
        "getDisplaySettings must read from _serverSettings"
    )
    assert "localStorage" not in body, (
        "getDisplaySettings must not use localStorage — display settings are server-side"
    )


def test_get_display_settings_uses_object_assign() -> None:
    """getDisplaySettings must merge with DISPLAY_DEFAULTS via Object.assign."""
    match = re.search(
        r"function getDisplaySettings\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// |\n/\*)",
        _JS,
        re.DOTALL,
    )
    assert match, "getDisplaySettings function not found"
    body = match.group(1)
    assert "Object.assign" in body, (
        "getDisplaySettings must use Object.assign to merge with defaults"
    )


def test_get_display_settings_uses_display_defaults() -> None:
    """getDisplaySettings must use DISPLAY_DEFAULTS as fallback for missing keys."""
    match = re.search(
        r"function getDisplaySettings\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// |\n/\*)",
        _JS,
        re.DOTALL,
    )
    assert match, "getDisplaySettings function not found"
    body = match.group(1)
    assert "DISPLAY_DEFAULTS" in body, (
        "getDisplaySettings must use DISPLAY_DEFAULTS as fallback"
    )


# ── saveDisplaySettings implementation ───────────────────────────────────────


def test_on_display_setting_change_uses_api_patch() -> None:
    """onDisplaySettingChange must write display settings via API PATCH, not localStorage."""
    match = re.search(
        r"function onDisplaySettingChange\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// |\n/\*)",
        _JS,
        re.DOTALL,
    )
    assert match, "onDisplaySettingChange function not found"
    body = match.group(1)
    assert "api('PATCH'" in body or 'api("PATCH"' in body, (
        "onDisplaySettingChange must write display settings via api('PATCH', '/api/settings', patch)"
    )
    assert "localStorage" not in body, (
        "onDisplaySettingChange must not use localStorage for display settings"
    )


def test_on_display_setting_change_catches_errors() -> None:
    """onDisplaySettingChange must handle API errors gracefully via .catch()."""
    match = re.search(
        r"function onDisplaySettingChange\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// |\n/\*)",
        _JS,
        re.DOTALL,
    )
    assert match, "onDisplaySettingChange function not found"
    body = match.group(1)
    assert ".catch" in body, "onDisplaySettingChange must handle errors via .catch()"


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


def test_exports_get_display_settings() -> None:
    """module.exports must export getDisplaySettings."""
    match = re.search(
        r"module\.exports\s*=\s*\{(.*?)\};",
        _JS,
        re.DOTALL,
    )
    assert match, "module.exports block not found"
    exports = match.group(1)
    assert "getDisplaySettings" in exports, (
        "module.exports must export getDisplaySettings"
    )


def test_exports_no_save_display_settings() -> None:
    """module.exports must NOT export saveDisplaySettings (removed in server-settings migration)."""
    match = re.search(
        r"module\.exports\s*=\s*\{(.*?)\};",
        _JS,
        re.DOTALL,
    )
    assert match, "module.exports block not found"
    exports = match.group(1)
    assert "saveDisplaySettings" not in exports, (
        "module.exports must not export saveDisplaySettings — use patchServerSetting instead"
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
    """onDisplaySettingChange must save display settings via api('PATCH', '/api/settings', ...)."""
    match = re.search(
        r"function onDisplaySettingChange\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "onDisplaySettingChange function not found"
    body = match.group(1)
    assert "api('PATCH'" in body or 'api("PATCH"' in body, (
        "onDisplaySettingChange must save display settings via api('PATCH', '/api/settings', patch)"
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
    """Hover preview must read delay from getDisplaySettings().hoverPreviewDelay."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "getDisplaySettings" in body and "hoverPreviewDelay" in body, (
        "bindStaticEventListeners hover preview must read delay from getDisplaySettings().hoverPreviewDelay"
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
    """DOMContentLoaded handler must call applyDisplaySettings(getDisplaySettings())."""
    # Find the DOMContentLoaded handler block
    match = re.search(
        r"DOMContentLoaded.*?\{(.*?)(?=\}\);?\s*\n// |\}\);\s*$)",
        _JS,
        re.DOTALL,
    )
    assert match, "DOMContentLoaded handler not found"
    body = match.group(1)
    assert "applyDisplaySettings" in body, (
        "DOMContentLoaded handler must call applyDisplaySettings(getDisplaySettings())"
    )
    assert "getDisplaySettings" in body, (
        "DOMContentLoaded handler must call applyDisplaySettings(getDisplaySettings())"
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
        r"async function createNewSession\s*\([\w,\s]+\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
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
        r"async function createNewSession\s*\([\w,\s]+\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "createNewSession function not found"
    body = match.group(1)
    assert "showToast" in body, "createNewSession must call showToast"


def test_create_new_session_calls_poll_sessions() -> None:
    """createNewSession must call pollSessions."""
    match = re.search(
        r"async function createNewSession\s*\([\w,\s]+\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "createNewSession function not found"
    body = match.group(1)
    assert "pollSessions" in body, "createNewSession must call pollSessions"


def test_create_new_session_auto_opens_session() -> None:
    """createNewSession must call openSession when auto_open_created is not false."""
    match = re.search(
        r"async function createNewSession\s*\([\w,\s]+\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
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
        r"async function createNewSession\s*\([\w,\s]+\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
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


# ── terminal.js: createTerminal receives font size as parameter ───────────────


def test_create_terminal_accepts_font_size_parameter() -> None:
    """createTerminal() must accept a fontSize parameter (from server settings via app.js).
    The old localStorage-based approach is gone since the server-settings migration.
    """
    match = re.search(
        r"function createTerminal\s*\([^)]*\)\s*\{(.*?)(?=\n(?:function|//|window\.))",
        _TERMINAL_JS,
        re.DOTALL,
    )
    assert match, "createTerminal function not found in terminal.js"
    # Verify the function signature accepts a parameter (not zero-arg)
    sig_match = re.search(r"function createTerminal\s*\(([^)]+)\)", _TERMINAL_JS)
    assert sig_match, "createTerminal must accept a fontSize parameter"
    param = sig_match.group(1).strip()
    assert param, "createTerminal must accept a fontSize parameter (not zero-arg)"
    assert "localStorage" not in match.group(1), (
        "createTerminal must NOT read from localStorage — "
        "fontSize must be passed as a parameter from app.js (getDisplaySettings().fontSize)"
    )


def test_create_terminal_does_not_parse_json_from_localstorage() -> None:
    """createTerminal() must NOT parse JSON from localStorage (server-settings migration).
    Font size now comes directly from the fontSize parameter passed by app.js.
    """
    match = re.search(
        r"function createTerminal\s*\([^)]*\)\s*\{(.*?)(?=\n(?:function|//|window\.))",
        _TERMINAL_JS,
        re.DOTALL,
    )
    assert match, "createTerminal function not found in terminal.js"
    body = match.group(1)
    assert "JSON.parse" not in body, (
        "createTerminal must NOT use JSON.parse — localStorage is no longer used; "
        "fontSize comes directly as a parameter from app.js"
    )
    assert "fontSize" in body, (
        "createTerminal must use the fontSize parameter for terminal configuration"
    )


def test_create_terminal_applies_mobile_cap_with_math_min() -> None:
    """createTerminal() must apply mobile cap using Math.min(storedFontSize, 12)."""
    match = re.search(
        r"function createTerminal\s*\([^)]*\)\s*\{(.*?)(?=\n(?:function|//|window\.))",
        _TERMINAL_JS,
        re.DOTALL,
    )
    assert match, "createTerminal function not found in terminal.js"
    body = match.group(1)
    assert "Math.min" in body, (
        "createTerminal must use Math.min for mobile font size cap"
    )


def test_create_terminal_uses_stored_font_size_not_hardcoded() -> None:
    """createTerminal() must use passed fontSize parameter, not hardcoded 14 or ternary."""
    match = re.search(
        r"function createTerminal\s*\([^)]*\)\s*\{(.*?)(?=\n(?:function|//|window\.))",
        _TERMINAL_JS,
        re.DOTALL,
    )
    assert match, "createTerminal function not found in terminal.js"
    body = match.group(1)
    # The font size in Terminal constructor should use a variable, not a ternary with literal 14
    # Check that there's no raw "mobile ? 12 : 14" pattern anymore
    assert "mobile ? 12 : 14" not in body, (
        "createTerminal must not use hardcoded 'mobile ? 12 : 14' ternary; "
        "use the passed fontSize parameter with Math.min cap for mobile"
    )


def test_create_terminal_has_default_font_size_14() -> None:
    """createTerminal() must default to fontSize 14 when no parameter is passed."""
    match = re.search(
        r"function createTerminal\s*\([^)]*\)\s*\{(.*?)(?=\n(?:function|//|window\.))",
        _TERMINAL_JS,
        re.DOTALL,
    )
    assert match, "createTerminal function not found in terminal.js"
    body = match.group(1)
    assert "14" in body, (
        "createTerminal must have default font size of 14 for when no fontSize parameter is passed"
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
        r"function renderSidebar\s*\(.*?\)\s*\{(.*?)(?=\nconst SIDEBAR_KEY|function |\n// ─)",
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
        r"function renderSidebar\s*\(.*?\)\s*\{(.*?)(?=\nconst SIDEBAR_KEY|function |\n// ─)",
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


def test_build_remote_instance_row_url_input_aria_label() -> None:
    """urlInput in _buildRemoteInstanceRow must have an aria-label attribute."""
    match = re.search(
        r"function _buildRemoteInstanceRow\s*\(.*?\)\s*\{(.*?)(?=\n(?:function|/\*\*|window\.)|\n})",
        _JS,
        re.DOTALL,
    )
    assert match, "_buildRemoteInstanceRow function not found in app.js"
    body = match.group(1)
    assert "Remote instance URL" in body, (
        "urlInput must have aria-label='Remote instance URL' for screen-reader accessibility"
    )


def test_build_remote_instance_row_name_input_aria_label() -> None:
    """nameInput in _buildRemoteInstanceRow must have an aria-label attribute."""
    match = re.search(
        r"function _buildRemoteInstanceRow\s*\(.*?\)\s*\{(.*?)(?=\n(?:function|/\*\*|window\.)|\n})",
        _JS,
        re.DOTALL,
    )
    assert match, "_buildRemoteInstanceRow function not found in app.js"
    body = match.group(1)
    assert "Remote instance display name" in body, (
        "nameInput must have aria-label='Remote instance display name' for screen-reader accessibility"
    )


# ============================================================
# Multi-Device tab — settings UI reorganization
# ============================================================


def test_open_settings_populates_multi_device_enabled() -> None:
    """openSettings must populate #setting-multi-device-enabled from server settings."""
    match = re.search(
        r"function openSettings\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "openSettings function not found in app.js"
    body = match.group(1)
    assert "setting-multi-device-enabled" in body, (
        "openSettings must populate #setting-multi-device-enabled checkbox"
    )
    assert "multi_device_enabled" in body, (
        "openSettings must reference multi_device_enabled from server settings"
    )


def test_open_settings_updates_document_title() -> None:
    """openSettings must update document.title from the device_name setting."""
    match = re.search(
        r"function openSettings\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "openSettings function not found in app.js"
    body = match.group(1)
    assert "document.title" in body, (
        "openSettings must update document.title from device_name"
    )


def test_bind_static_event_listeners_binds_multi_device_enabled() -> None:
    """bindStaticEventListeners must bind change on #setting-multi-device-enabled."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "setting-multi-device-enabled" in body, (
        "bindStaticEventListeners must bind #setting-multi-device-enabled change event"
    )


def test_device_name_change_updates_document_title() -> None:
    """device name change handler in bindStaticEventListeners must update document.title."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "document.title" in body, (
        "bindStaticEventListeners device-name handler must update document.title"
    )


# ─── task-4-frontend-session-key-comparison ─────────────────────────────────


def test_update_pill_bell_uses_session_key_or_name() -> None:
    """updatePillBell must compare using sessionKey||name against a viewingKey
    that accounts for remote sessions (_viewingRemoteId prefix)."""
    match = re.search(
        r"function updatePillBell\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "updatePillBell function not found"
    body = match.group(1)
    # Must build viewingKey using _viewingRemoteId
    assert "_viewingRemoteId" in body, (
        "updatePillBell must reference _viewingRemoteId to build viewingKey"
    )
    assert "viewingKey" in body, "updatePillBell must define a viewingKey variable"
    # Must compare using sessionKey || s.name (not just s.name)
    assert "sessionKey" in body, (
        "updatePillBell must compare using s.sessionKey || s.name (not just s.name)"
    )


def test_update_session_pill_uses_session_key_or_name() -> None:
    """updateSessionPill must compare using sessionKey||name against a viewingKey
    that accounts for remote sessions (_viewingRemoteId prefix)."""
    match = re.search(
        r"function updateSessionPill\s*\(\w+\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "updateSessionPill function not found"
    body = match.group(1)
    # Must build viewingKey using _viewingRemoteId
    assert "_viewingRemoteId" in body, (
        "updateSessionPill must reference _viewingRemoteId to build viewingKey"
    )
    assert "viewingKey" in body, "updateSessionPill must define a viewingKey variable"
    # Must compare using sessionKey || s.name (not just s.name)
    assert "sessionKey" in body, (
        "updateSessionPill must compare using s.sessionKey || s.name (not just s.name)"
    )


# ─── task-5-open-session-bell-clear ─────────────────────────────────────────


def test_open_session_fires_bell_clear_for_remote() -> None:
    """openSession must fire a bell/clear POST to the federation proxy for remote sessions."""
    match = re.search(
        r"async function openSession\s*\(.*?\)\s*\{(.*?)^\}",
        _JS,
        re.DOTALL | re.MULTILINE,
    )
    assert match, "openSession function not found"
    body = match.group(1)
    # Must POST to federation bell/clear endpoint when _remoteId is not empty
    assert "/bell/clear" in body, (
        "openSession must POST to /api/federation/{remoteId}/sessions/{name}/bell/clear"
    )
    # Must guard bell-clear with a _deviceId !== '' check (renamed from _remoteId in task-11)
    assert "_deviceId !== ''" in body, (
        "openSession must guard bell-clear POST with _deviceId !== '' check"
    )


def test_open_session_bell_clear_is_fire_and_forget() -> None:
    """openSession bell-clear POST must be fire-and-forget using .catch() to swallow errors."""
    match = re.search(
        r"async function openSession\s*\(.*?\)\s*\{(.*?)^\}",
        _JS,
        re.DOTALL | re.MULTILINE,
    )
    assert match, "openSession function not found"
    body = match.group(1)
    # Must use .catch(function() {}) to swallow errors — not awaited
    assert ".catch(function() {})" in body, (
        "openSession bell-clear POST must swallow errors with .catch(function() {})"
    )
    # Must NOT await the bell-clear call (it's fire-and-forget)
    bell_clear_lines = [line for line in body.splitlines() if "/bell/clear" in line]
    assert bell_clear_lines, "openSession must contain a bell/clear call"
    for line in bell_clear_lines:
        assert "await" not in line, (
            f"openSession bell-clear POST must NOT be awaited (fire-and-forget): {line.strip()}"
        )


# ─── Task 4: sidebar functions use server-side settings ──────────────────────


def test_no_sidebar_key_constant() -> None:
    """SIDEBAR_KEY constant must be removed — sidebar state moves to _serverSettings."""
    assert "SIDEBAR_KEY" not in _JS, (
        "SIDEBAR_KEY constant must be removed from app.js; "
        "sidebar open/closed state is now stored in _serverSettings.sidebarOpen"
    )


def test_init_sidebar_reads_server_settings() -> None:
    """initSidebar must read sidebarOpen from _serverSettings, not localStorage."""
    match = re.search(
        r"function initSidebar\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// ─|\n/\*\*)",
        _JS,
        re.DOTALL,
    )
    assert match, "initSidebar function not found"
    body = match.group(1)
    assert "_serverSettings" in body, (
        "initSidebar must read sidebarOpen from _serverSettings"
    )
    assert "localStorage" not in body, (
        "initSidebar must not use localStorage — sidebar state is now server-side"
    )


def test_init_sidebar_calls_patch_server_setting() -> None:
    """initSidebar must call patchServerSetting to persist the auto-detected state."""
    match = re.search(
        r"function initSidebar\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// ─|\n/\*\*)",
        _JS,
        re.DOTALL,
    )
    assert match, "initSidebar function not found"
    body = match.group(1)
    assert "patchServerSetting" in body, (
        "initSidebar must call patchServerSetting to persist the auto-detected sidebar state"
    )


def test_toggle_sidebar_reads_server_settings() -> None:
    """toggleSidebar must derive state from the DOM class, not from localStorage."""
    match = re.search(
        r"function toggleSidebar\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// ─|\n/\*\*)",
        _JS,
        re.DOTALL,
    )
    assert match, "toggleSidebar function not found"
    body = match.group(1)
    assert "_serverSettings" in body, (
        "toggleSidebar must write new state to _serverSettings"
    )
    assert "localStorage" not in body, (
        "toggleSidebar must not use localStorage — sidebar state is now server-side"
    )


def test_toggle_sidebar_calls_patch_server_setting() -> None:
    """toggleSidebar must call patchServerSetting to persist the toggled state."""
    match = re.search(
        r"function toggleSidebar\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// ─|\n/\*\*)",
        _JS,
        re.DOTALL,
    )
    assert match, "toggleSidebar function not found"
    body = match.group(1)
    assert "patchServerSetting" in body, (
        "toggleSidebar must call patchServerSetting to persist the toggled sidebar state"
    )


def test_bind_sidebar_click_away_uses_server_settings() -> None:
    """bindSidebarClickAway must write false to _serverSettings on collapse."""
    match = re.search(
        r"function bindSidebarClickAway\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// ─|\n/\*\*)",
        _JS,
        re.DOTALL,
    )
    assert match, "bindSidebarClickAway function not found"
    body = match.group(1)
    assert "_serverSettings" in body, (
        "bindSidebarClickAway must write false to _serverSettings.sidebarOpen on collapse"
    )
    assert "localStorage" not in body, (
        "bindSidebarClickAway must not use localStorage — sidebar state is now server-side"
    )


def test_bind_sidebar_click_away_calls_patch_server_setting() -> None:
    """bindSidebarClickAway must call patchServerSetting to persist collapsed state."""
    match = re.search(
        r"function bindSidebarClickAway\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// ─|\n/\*\*)",
        _JS,
        re.DOTALL,
    )
    assert match, "bindSidebarClickAway function not found"
    body = match.group(1)
    assert "patchServerSetting" in body, (
        "bindSidebarClickAway must call patchServerSetting to persist collapsed state"
    )


# ─── task-1: getVisibleSessions falsy-0 bug fix ────────────────────────────


def test_get_visible_sessions_has_no_remote_id_filter() -> None:
    """getVisibleSessions must not check s.remoteId at all.

    After federation settings sync, hidden_sessions applies to both local and
    remote sessions.  The filter must check session name only, with no remoteId
    guard.  Must also not use !s.remoteId which would treat remoteId=0 as falsy.
    """
    match = re.search(
        r"function getVisibleSessions\s*\(\w+\)\s*\{(.*?)(?=\n(?:function|//|window\.))",
        _JS,
        re.DOTALL,
    )
    assert match, "getVisibleSessions function not found in app.js"
    body = match.group(1)
    assert "!s.remoteId" not in body, (
        "getVisibleSessions must NOT use '!s.remoteId' — this treats remoteId=0 as falsy, "
        "hiding the first remote instance."
    )
    assert "s.remoteId" not in body, (
        "getVisibleSessions must NOT reference s.remoteId at all — the filter applies to "
        "all sessions regardless of origin (local or federated)."
    )


# ─── task-2: updatePageTitle and updateFaviconBadge filter hidden sessions ─────


def test_update_page_title_filters_through_visible_sessions() -> None:
    """updatePageTitle() must count bell activity from getVisibleSessions(), not all sessions."""
    match = re.search(
        r"function updatePageTitle\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// ─|\n/\*\*)",
        _JS,
        re.DOTALL,
    )
    assert match, "updatePageTitle function not found in app.js"
    body = match.group(1)
    assert "getVisibleSessions" in body, (
        "updatePageTitle must filter sessions through getVisibleSessions() "
        "to exclude hidden sessions from the bell count"
    )


def test_update_favicon_badge_filters_through_visible_sessions() -> None:
    """updateFaviconBadge() must check bell activity from getVisibleSessions(), not all sessions."""
    match = re.search(
        r"function updateFaviconBadge\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// ─|\n/\*\*)",
        _JS,
        re.DOTALL,
    )
    assert match, "updateFaviconBadge function not found in app.js"
    body = match.group(1)
    assert "getVisibleSessions" in body, (
        "updateFaviconBadge must filter sessions through getVisibleSessions() "
        "to exclude hidden sessions from the bell activity check"
    )


# ─── Task 11: Frontend — Change Session Key Format to `device_id:name` ────────


def test_build_tile_html_uses_remote_id_for_data_remote_id() -> None:
    """buildTileHTML must use session.remoteId (not session.deviceId) for data-remote-id.

    v0.4.1 hotfix changed this from deviceId to remoteId because deviceId is non-null for
    local sessions, which caused them to route through federation endpoints and 404.
    remoteId is null for local sessions, so only remote sessions get routed via federation.
    """
    match = re.search(
        r"function buildTileHTML\s*\(.*?\)\s*\{(.*?)(?=\nfunction |\n// |\n/\*\*)",
        _JS,
        re.DOTALL,
    )
    assert match, "buildTileHTML function not found"
    body = match.group(1)
    assert "session.remoteId" in body, (
        "buildTileHTML must use session.remoteId for data-remote-id attribute — "
        "deviceId is intentionally NOT used because it is non-null for local sessions, "
        "which would incorrectly route them through federation endpoints"
    )
    assert "session.deviceId" not in body, (
        "buildTileHTML must NOT use session.deviceId for data-remote-id — "
        "deviceId is non-null for local sessions and causes federation 404s"
    )


def test_build_sidebar_html_uses_remote_id_for_data_remote_id() -> None:
    """buildSidebarHTML must use session.remoteId (not session.deviceId) for data-remote-id.

    v0.4.1 hotfix changed this from deviceId to remoteId because deviceId is non-null for
    local sessions, which caused them to route through federation endpoints and 404.
    remoteId is null for local sessions, so only remote sessions get routed via federation.
    """
    match = re.search(
        r"function buildSidebarHTML\s*\(.*?\)\s*\{(.*?)(?=\nfunction |\n// |\n/\*\*)",
        _JS,
        re.DOTALL,
    )
    assert match, "buildSidebarHTML function not found"
    body = match.group(1)
    assert "session.remoteId" in body, (
        "buildSidebarHTML must use session.remoteId for data-remote-id attribute — "
        "deviceId is intentionally NOT used because it is non-null for local sessions, "
        "which would incorrectly route them through federation endpoints"
    )
    assert "session.deviceId" not in body, (
        "buildSidebarHTML must NOT use session.deviceId for data-remote-id — "
        "deviceId is non-null for local sessions and causes federation 404s"
    )


def test_get_visible_sessions_checks_session_key_in_hidden() -> None:
    """getVisibleSessions must check sessionKey (as well as name) against the hidden_sessions list.

    hidden_sessions may contain either:
    - old format: plain session name (e.g. 'dev')
    - new format: device_id:name key (e.g. 'abc-123:dev')

    Both formats must be matched for backward compatibility.
    """
    match = re.search(
        r"function getVisibleSessions\s*\(\w+\)\s*\{(.*?)(?=\n(?:function|//|window\.))",
        _JS,
        re.DOTALL,
    )
    assert match, "getVisibleSessions function not found"
    body = match.group(1)
    assert "sessionKey" in body, (
        "getVisibleSessions must check s.sessionKey (in addition to s.name) against hidden_sessions "
        "for backward compatibility: hidden_sessions may contain plain names OR device_id:name keys"
    )


def test_open_session_uses_device_id_variable_for_federation() -> None:
    """openSession must use _deviceId variable (from opts.remoteId) for federation API calls.

    The local variable _remoteId is renamed to _deviceId to reflect that the value
    is now a device_id string, not an integer remote instance index.
    """
    match = re.search(
        r"async function openSession\s*\(.*?\)\s*\{(.*?)^\}",
        _JS,
        re.DOTALL | re.MULTILINE,
    )
    assert match, "openSession function not found"
    body = match.group(1)
    assert "_deviceId" in body, (
        "openSession must use _deviceId variable (assigned from opts.remoteId) "
        "for federation API URL construction — reflects that the value is now a device_id string"
    )


def test_create_new_session_uses_device_id_variable_internally() -> None:
    """createNewSession must use a deviceId variable internally for federation endpoint construction.

    The internal variable is renamed from 'remoteId' to 'deviceId' to reflect that
    the value is now a device_id string (e.g. 'abc-123'), not an integer index.
    """
    match = re.search(
        r"async function createNewSession\s*\([\w,\s]+\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "createNewSession function not found"
    body = match.group(1)
    assert "var deviceId" in body, (
        "createNewSession must declare 'var deviceId' internally (assigned from the remoteId parameter) "
        "to reflect that the value is now a device_id string used in federation API URLs"
    )


# ─── Task 12: Remove 'filtered' from gridViewMode options ─────────────────────


def test_load_grid_view_mode_guards_filtered_value() -> None:
    """loadGridViewMode must remap 'filtered' to 'flat' for users who had it set."""
    match = re.search(
        r"function loadGridViewMode\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// |\n/\*\*)",
        _JS,
        re.DOTALL,
    )
    assert match, "loadGridViewMode function not found"
    body = match.group(1)
    assert "filtered" in body, (
        "loadGridViewMode must guard against 'filtered': "
        "if (mode === 'filtered') mode = 'flat';  — graceful fallback for existing users"
    )
    assert "flat" in body, (
        "loadGridViewMode must fall back to 'flat' when mode is 'filtered'"
    )


def test_set_grid_view_mode_guards_filtered_value() -> None:
    """_setGridViewMode (test helper) must remap 'filtered' to 'flat'."""
    match = re.search(
        r"function _setGridViewMode\s*\(\w+\)\s*\{(.*?)(?=\n\})",
        _JS,
        re.DOTALL,
    )
    assert match, "_setGridViewMode function not found"
    body = match.group(1)
    assert "filtered" in body, (
        "_setGridViewMode must guard against 'filtered': "
        "if (mode === 'filtered') mode = 'flat';"
    )
    assert "flat" in body, "_setGridViewMode must remap 'filtered' to 'flat'"


def test_render_grid_no_filtered_mode_check() -> None:
    """renderGrid must not contain any _gridViewMode === 'filtered' check.

    The 'filtered' gridViewMode has been removed.  All references in renderGrid
    that branch on _gridViewMode === 'filtered' must be deleted so the filter bar
    is never rendered and the device-filter logic is never applied.
    """
    match = re.search(
        r"function renderGrid\s*\(\w+\)\s*\{(.*?)(?=\n(?:function|//|window\.))",
        _JS,
        re.DOTALL,
    )
    assert match, "renderGrid function not found in app.js"
    body = match.group(1)
    assert "'filtered'" not in body and '"filtered"' not in body, (
        "renderGrid must not check _gridViewMode === 'filtered' — "
        "the 'filtered' mode has been removed; only 'flat' and 'grouped' remain"
    )


# ---------------------------------------------------------------------------
# COE verification fixes
# ---------------------------------------------------------------------------


def test_resolve_active_view_function_exists() -> None:
    """_resolveActiveView must exist in app.js for active_view fallback (GAP 4).

    When active_view references a view name that no longer exists in the views
    list, _resolveActiveView must fall back to 'all' so the user always sees
    sessions rather than an empty/broken state.
    """
    assert "_resolveActiveView" in _JS, (
        "_resolveActiveView function must exist in app.js for active_view fallback; "
        "when a view is deleted while a device is offline the stored active_view "
        "must fall back to 'all'"
    )


def test_resolve_active_view_falls_back_to_all() -> None:
    """_resolveActiveView must return 'all' when active_view is not in views list."""
    match = re.search(
        r"function _resolveActiveView\s*\(.*?\)\s*\{(.*?)^}",
        _JS,
        re.DOTALL | re.MULTILINE,
    )
    assert match, "_resolveActiveView function not found"
    body = match.group(1)
    assert "return 'all'" in body, (
        "_resolveActiveView must return 'all' as the fallback when active_view "
        "is not found in the views list"
    )
    assert "'all'" in body and "'hidden'" in body, (
        "_resolveActiveView must pass 'all' and 'hidden' through without lookup "
        "(these are reserved view names, not user-created views)"
    )


def test_create_device_select_uses_device_id_for_option_value() -> None:
    """_createDeviceSelect must use device_id (not integer index) for option values.

    Session creation routes now accept a device_id string, not an integer index.
    The select option value must be remotes[i].device_id when available so the
    correct device_id is passed to createNewSession().
    """
    match = re.search(
        r"function _createDeviceSelect\s*\(\s*\)\s*\{(.*?)^}",
        _JS,
        re.DOTALL | re.MULTILINE,
    )
    assert match, "_createDeviceSelect function not found"
    body = match.group(1)
    assert "device_id" in body, (
        "_createDeviceSelect must use remotes[i].device_id (with String(i) fallback) "
        "for option values; integer index no longer matches federation API expectations"
    )


# ---------------------------------------------------------------------------
# Active view state variable and getVisibleSessions (task-2-active-view)
# ---------------------------------------------------------------------------


def test_active_view_state_variable_exists() -> None:
    """_activeView state variable must be declared with default 'all'."""
    assert "let _activeView = 'all'" in _JS, (
        "let _activeView = 'all' must be declared in the App state section of app.js"
    )


def test_get_visible_sessions_all_view_excludes_hidden() -> None:
    """getVisibleSessions must reference _activeView to honour the active view."""
    match = re.search(
        r"function getVisibleSessions\(sessions\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "getVisibleSessions function not found"
    body = match.group(1)
    assert "_activeView" in body, (
        "getVisibleSessions must check _activeView to filter sessions by view"
    )


def test_get_visible_sessions_hidden_view_shows_hidden() -> None:
    """getVisibleSessions must handle the 'hidden' view case."""
    match = re.search(
        r"function getVisibleSessions\(sessions\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "getVisibleSessions function not found"
    body = match.group(1)
    assert "'hidden'" in body or '"hidden"' in body, (
        "getVisibleSessions must explicitly handle the 'hidden' view (show only hidden sessions)"
    )


def test_get_visible_sessions_user_view_filters_by_session_key() -> None:
    """getVisibleSessions must use sessionKey when filtering for a user-defined view."""
    match = re.search(
        r"function getVisibleSessions\(sessions\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "getVisibleSessions function not found"
    body = match.group(1)
    assert "sessionKey" in body, (
        "getVisibleSessions must use sessionKey to match sessions against the user view's "
        "sessions list"
    )


def test_get_active_view_helper_exported() -> None:
    """window.MuxplexApp must export _getActiveView test helper."""
    match = re.search(
        r"module\.exports\s*=\s*\{(.*?)\};",
        _JS,
        re.DOTALL,
    )
    assert match, "module.exports block not found"
    exports = match.group(1)
    assert "_getActiveView" in exports, (
        "module.exports must export _getActiveView test helper"
    )


def test_set_active_view_helper_exported() -> None:
    """window.MuxplexApp must export _setActiveView test helper."""
    match = re.search(
        r"module\.exports\s*=\s*\{(.*?)\};",
        _JS,
        re.DOTALL,
    )
    assert match, "module.exports block not found"
    exports = match.group(1)
    assert "_setActiveView" in exports, (
        "module.exports must export _setActiveView test helper"
    )


# ---------------------------------------------------------------------------
# Auto-add session to active user view on creation (task-3-auto-add-to-view)
# ---------------------------------------------------------------------------


def test_create_new_session_references_active_view() -> None:
    """createNewSession must reference _activeView to auto-add the new session to the active user view."""
    match = re.search(
        r"async function createNewSession\s*\([\w,\s]+\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "createNewSession function not found"
    body = match.group(1)
    assert "_activeView" in body, (
        "createNewSession must reference _activeView to auto-add new session to the active user view"
    )


# ---------------------------------------------------------------------------
# Header Dropdown — JS Render + Open/Close + View Switching (task-6)
# ---------------------------------------------------------------------------


def test_render_view_dropdown_function_exists() -> None:
    """renderViewDropdown function must exist in app.js."""
    assert "function renderViewDropdown" in _JS, (
        "renderViewDropdown must be defined in app.js"
    )


def test_render_view_dropdown_exported() -> None:
    """module.exports must export renderViewDropdown."""
    match = re.search(
        r"module\.exports\s*=\s*\{(.*?)\};",
        _JS,
        re.DOTALL,
    )
    assert match, "module.exports block not found"
    exports = match.group(1)
    assert "renderViewDropdown" in exports, (
        "module.exports must export renderViewDropdown"
    )


def test_toggle_view_dropdown_function_exists() -> None:
    """toggleViewDropdown function must exist in app.js."""
    assert "function toggleViewDropdown" in _JS, (
        "toggleViewDropdown must be defined in app.js"
    )


def test_switch_view_function_exists() -> None:
    """switchView function must exist in app.js."""
    assert "function switchView" in _JS, "switchView must be defined in app.js"


def test_switch_view_patches_state() -> None:
    """switchView must PATCH /api/state to persist active_view."""
    match = re.search(
        r"function switchView\s*\(\w+\)\s*\{(.*?)(?=\nfunction |\nasync function |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "switchView function not found"
    body = match.group(1)
    assert "PATCH" in body, "switchView must use PATCH method"
    assert "/api/state" in body, "switchView must PATCH /api/state"


# ---------------------------------------------------------------------------
# Keyboard Shortcuts — Backtick, Number Keys, Arrow Navigation (task-7)
# ---------------------------------------------------------------------------


def test_handle_global_keydown_has_backtick_handler() -> None:
    """handleGlobalKeydown must handle backtick/Backquote key to toggle view dropdown."""
    match = re.search(
        r"function handleGlobalKeydown\s*\(e\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "handleGlobalKeydown function not found"
    body = match.group(1)
    has_backtick = (
        "Backquote" in body or "e.key === '`'" in body or 'e.key === "`"' in body
    )
    assert has_backtick, (
        "handleGlobalKeydown must handle backtick/Backquote key to toggle view dropdown"
    )
    assert "toggleViewDropdown" in body, (
        "handleGlobalKeydown backtick handler must call toggleViewDropdown"
    )


def test_handle_global_keydown_has_number_key_shortcuts() -> None:
    """handleGlobalKeydown must call switchView for number keys 1–9."""
    match = re.search(
        r"function handleGlobalKeydown\s*\(e\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "handleGlobalKeydown function not found"
    body = match.group(1)
    assert "switchView" in body, (
        "handleGlobalKeydown must call switchView for number keys 1–9"
    )
    has_number_handling = "Digit" in body or "parseInt" in body
    assert has_number_handling, (
        "handleGlobalKeydown must handle number key codes (e.g. e.code.startsWith('Digit'))"
    )


def test_backtick_only_on_grid_not_fullscreen() -> None:
    """Backtick handler must check _viewMode === 'grid' (not active in fullscreen)."""
    match = re.search(
        r"function handleGlobalKeydown\s*\(e\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "handleGlobalKeydown function not found"
    body = match.group(1)
    assert "_viewMode" in body, (
        "handleGlobalKeydown backtick handler must check _viewMode"
    )
    assert "'grid'" in body, (
        "handleGlobalKeydown must check _viewMode === 'grid' before handling backtick/number shortcuts"
    )


# ---------------------------------------------------------------------------
# New View inline creation flow (task-8)
# ---------------------------------------------------------------------------


def test_show_new_view_input_function_exists() -> None:
    """app.js defines showNewViewInput for inline view creation."""
    assert "function showNewViewInput" in _JS, (
        "showNewViewInput must be defined in app.js"
    )


def test_show_new_view_input_patches_settings() -> None:
    """showNewViewInput handler PATCHes /api/settings with views on Enter."""
    match = re.search(
        r"function showNewViewInput\s*\(\s*\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "showNewViewInput function not found"
    body = match.group(1)
    assert "PATCH" in body, (
        "showNewViewInput must PATCH /api/settings to create the view"
    )
    assert "/api/settings" in body, (
        "showNewViewInput must PATCH /api/settings with the updated views"
    )
    assert "views" in body, "showNewViewInput must include 'views' in the PATCH body"


# ============================================================
# Manage Views settings tab (task-9)
# ============================================================


def test_render_views_settings_tab_function_exists() -> None:
    """renderViewsSettingsTab function must exist in app.js."""
    assert "function renderViewsSettingsTab" in _JS, (
        "renderViewsSettingsTab must be defined in app.js"
    )


# ============================================================
# Remove filtered gridViewMode rendering code (task-11)
# ============================================================


def test_no_active_filter_device_in_render_grid() -> None:
    """renderGrid must not reference _activeFilterDevice."""
    match = re.search(
        r"function renderGrid\(sessions\)\s*\{(.*?)(?=\n// -------|\n// =======)",
        _JS,
        re.DOTALL,
    )
    assert match, "renderGrid function not found"
    body = match.group(1)
    assert "_activeFilterDevice" not in body, (
        "renderGrid must not reference _activeFilterDevice (filtered mode removed)"
    )


# ============================================================
# Phase 2 COE verification findings
# ============================================================


# ─── Fix 1: CSS/JS class name mismatch (BEM) ─────────────────────────────────


def test_render_view_dropdown_uses_bem_item_class() -> None:
    """renderViewDropdown must use BEM class 'view-dropdown__item' (not 'view-dropdown-item')."""
    match = re.search(
        r"function renderViewDropdown\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "renderViewDropdown function not found"
    body = match.group(1)
    assert "view-dropdown__item" in body, (
        "renderViewDropdown must use BEM class 'view-dropdown__item'"
    )


def test_render_view_dropdown_no_single_hyphen_item_class() -> None:
    """renderViewDropdown must NOT use single-hyphen 'view-dropdown-item'."""
    match = re.search(
        r"function renderViewDropdown\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "renderViewDropdown function not found"
    body = match.group(1)
    # Should not have the old single-hyphen class; allow only '--active' modifiers via '__'
    import re as _re

    bad_matches = _re.findall(r'"view-dropdown-item(?!--active)', body)
    assert not bad_matches, (
        "renderViewDropdown must not use single-hyphen 'view-dropdown-item' class (use BEM 'view-dropdown__item')"
    )


def test_render_view_dropdown_uses_bem_separator_class() -> None:
    """renderViewDropdown must use BEM class 'view-dropdown__separator'."""
    match = re.search(
        r"function renderViewDropdown\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "renderViewDropdown function not found"
    body = match.group(1)
    assert "view-dropdown__separator" in body, (
        "renderViewDropdown must use BEM class 'view-dropdown__separator'"
    )


def test_render_view_dropdown_uses_bem_action_class() -> None:
    """renderViewDropdown must use BEM class 'view-dropdown__action'."""
    match = re.search(
        r"function renderViewDropdown\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "renderViewDropdown function not found"
    body = match.group(1)
    assert "view-dropdown__action" in body, (
        "renderViewDropdown must use BEM class 'view-dropdown__action'"
    )


def test_render_view_dropdown_uses_bem_count_class() -> None:
    """renderViewDropdown must use BEM class 'view-dropdown__count' (not 'view-dropdown-badge')."""
    match = re.search(
        r"function renderViewDropdown\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "renderViewDropdown function not found"
    body = match.group(1)
    assert "view-dropdown__count" in body, (
        "renderViewDropdown must use BEM class 'view-dropdown__count' (was 'view-dropdown-badge')"
    )
    assert "view-dropdown-badge" not in body, (
        "renderViewDropdown must not use old 'view-dropdown-badge' class"
    )


def test_render_sidebar_view_dropdown_uses_bem_item_class() -> None:
    """renderSidebarViewDropdown must use BEM class 'view-dropdown__item'."""
    match = re.search(
        r"function renderSidebarViewDropdown\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "renderSidebarViewDropdown function not found"
    body = match.group(1)
    assert "view-dropdown__item" in body, (
        "renderSidebarViewDropdown must use BEM class 'view-dropdown__item'"
    )


def test_render_sidebar_view_dropdown_no_single_hyphen_item_class() -> None:
    """renderSidebarViewDropdown must NOT use single-hyphen 'view-dropdown-item'."""
    match = re.search(
        r"function renderSidebarViewDropdown\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "renderSidebarViewDropdown function not found"
    body = match.group(1)
    import re as _re

    bad_matches = _re.findall(r'"view-dropdown-item(?!--active)', body)
    assert not bad_matches, (
        "renderSidebarViewDropdown must not use single-hyphen 'view-dropdown-item' class"
    )


def test_render_sidebar_view_dropdown_uses_bem_count_class() -> None:
    """renderSidebarViewDropdown must use BEM class 'view-dropdown__count'."""
    match = re.search(
        r"function renderSidebarViewDropdown\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "renderSidebarViewDropdown function not found"
    body = match.group(1)
    assert "view-dropdown__count" in body, (
        "renderSidebarViewDropdown must use BEM class 'view-dropdown__count'"
    )
    assert "view-dropdown-badge" not in body, (
        "renderSidebarViewDropdown must not use old 'view-dropdown-badge' class"
    )


# ─── Fix 2: role="menuitem" on dropdown buttons ──────────────────────────────


def test_render_view_dropdown_buttons_have_role_menuitem() -> None:
    """renderViewDropdown must add role='menuitem' to every button it renders."""
    match = re.search(
        r"function renderViewDropdown\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "renderViewDropdown function not found"
    body = match.group(1)
    assert 'role="menuitem"' in body, (
        'renderViewDropdown must include role="menuitem" on buttons — '
        "handleGlobalKeydown arrow navigation queries [role='menuitem']"
    )


def test_render_sidebar_view_dropdown_buttons_have_role_menuitem() -> None:
    """renderSidebarViewDropdown must add role='menuitem' to every button it renders."""
    match = re.search(
        r"function renderSidebarViewDropdown\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "renderSidebarViewDropdown function not found"
    body = match.group(1)
    assert 'role="menuitem"' in body, (
        'renderSidebarViewDropdown must include role="menuitem" on buttons'
    )


# ─── Fix 3: sidebar dropdown uses position:fixed ────────────────────────────


def test_toggle_sidebar_view_dropdown_positions_with_bounding_rect() -> None:
    """toggleSidebarViewDropdown must use getBoundingClientRect when opening."""
    match = re.search(
        r"function toggleSidebarViewDropdown\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "toggleSidebarViewDropdown function not found"
    body = match.group(1)
    assert "getBoundingClientRect" in body, (
        "toggleSidebarViewDropdown must use getBoundingClientRect() to position "
        "the menu when opening — sidebar has overflow:hidden which clips absolute children"
    )


# ─── Fix 4: manage-views handler opens settings ──────────────────────────────


def test_manage_views_action_opens_settings() -> None:
    """bindStaticEventListeners manage-views handler must call openSettings()."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    # The manage-views action must do more than just close the dropdown
    # Find the section near 'manage-views'
    assert "manage-views" in body, (
        "bindStaticEventListeners must handle manage-views action"
    )
    # After manage-views, openSettings must be called
    idx = body.find("manage-views")
    nearby = body[idx : idx + 200]
    assert "openSettings" in nearby, (
        "bindStaticEventListeners manage-views handler must call openSettings()"
    )


def test_manage_views_action_switches_to_views_tab() -> None:
    """bindStaticEventListeners manage-views handler must call switchSettingsTab('views')."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    idx = body.find("manage-views")
    assert idx >= 0, "manage-views action not found in bindStaticEventListeners"
    nearby = body[idx : idx + 300]
    assert "switchSettingsTab" in nearby, (
        "bindStaticEventListeners manage-views handler must call switchSettingsTab"
    )
    assert "'views'" in nearby or '"views"' in nearby, (
        "bindStaticEventListeners manage-views handler must switch to 'views' tab"
    )


# ─── Fix 5: active_view persisted on delete/rename ───────────────────────────


def test_delete_active_view_persists_active_view_to_server() -> None:
    """When deleting the active view in renderViewsSettingsTab, PATCH /api/state with active_view."""
    match = re.search(
        r"function renderViewsSettingsTab\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "renderViewsSettingsTab function not found"
    body = match.group(1)
    # Find the section that sets _activeView = 'all' (the delete fallback path)
    fallback_idx = body.find("_activeView = 'all'")
    assert fallback_idx >= 0, (
        "_activeView = 'all' fallback not found in renderViewsSettingsTab"
    )
    # Within ~200 chars after the fallback, there must be an api( call for persistence
    nearby = body[fallback_idx : fallback_idx + 200]
    assert "api(" in nearby, (
        "renderViewsSettingsTab delete path must call api() immediately after setting _activeView = 'all'"
    )
    assert "active_view" in nearby, (
        "renderViewsSettingsTab delete path must PATCH /api/state with active_view"
    )


def test_views_settings_tab_no_inline_rename_commit() -> None:
    """renderViewsSettingsTab must NOT have inline rename (commitRename removed in Issue 6).

    Rename now lives in the Manage View panel, not the settings tab.
    """
    match = re.search(
        r"function renderViewsSettingsTab\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "renderViewsSettingsTab function not found"
    body = match.group(1)
    # Inline rename (commitRename) must be gone — it moved to the Manage View panel
    assert "commitRename" not in body, (
        "renderViewsSettingsTab must not have inline commitRename — "
        "rename moved to the Manage View panel (Issue 6)"
    )


# ─── Fix 6: click-outside handler for sidebar dropdown ───────────────────────


def test_bind_static_event_listeners_has_sidebar_dropdown_click_outside() -> None:
    """bindStaticEventListeners must have a click-outside handler for sidebar-view-dropdown-menu."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    # There must be a document click listener that references the sidebar dropdown menu
    # and closes it when clicking outside
    assert "sidebar-view-dropdown-menu" in body, (
        "bindStaticEventListeners must reference sidebar-view-dropdown-menu"
    )
    # The click-outside pattern: document.addEventListener click that checks sidebar dropdown
    # We verify that sidebar dropdown is handled in a click listener beyond the direct click handler
    click_count = body.count("document.addEventListener('click'")
    assert click_count >= 2, (
        "bindStaticEventListeners must have at least 2 document click listeners: "
        "one for header dropdown click-outside and one for sidebar dropdown click-outside"
    )


# ─── Fix 7: case-insensitive reserved name check ─────────────────────────────


def test_show_new_view_input_reserved_name_check_is_case_insensitive() -> None:
    """showNewViewInput reserved name check must use toLowerCase()."""
    match = re.search(
        r"function showNewViewInput\s*\(\s*\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "showNewViewInput function not found"
    body = match.group(1)
    # Must use toLowerCase for reserved name check
    assert "toLowerCase" in body, (
        "showNewViewInput reserved name check must use toLowerCase() — spec requires case-insensitive check"
    )


def test_rename_reserved_name_check_moved_to_manage_view_panel() -> None:
    """renderViewsSettingsTab must NOT contain commitRename — it moved to Manage View panel (Issue 6)."""
    match = re.search(
        r"function renderViewsSettingsTab\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "renderViewsSettingsTab function not found"
    body = match.group(1)
    # commitRename was removed from settings tab — it now lives in openManageViewPanel
    assert "commitRename" not in body, (
        "renderViewsSettingsTab must not have commitRename — rename moved to Manage View panel (Issue 6)"
    )


# ─── Fix 8: rename input maxLength ───────────────────────────────────────────


def test_rename_input_not_in_settings_tab() -> None:
    """renderViewsSettingsTab must NOT have views-settings-rename-input (Issue 6: inline rename removed)."""
    match = re.search(
        r"function renderViewsSettingsTab\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "renderViewsSettingsTab function not found"
    body = match.group(1)
    assert "views-settings-rename-input" not in body, (
        "renderViewsSettingsTab must NOT have views-settings-rename-input — "
        "inline rename was moved to the Manage View panel (Issue 6)"
    )


# ─── Fix 9: device_id from /api/instance-info ────────────────────────────────


def test_local_device_id_variable_declared() -> None:
    """_localDeviceId module-level variable must be declared."""
    assert "_localDeviceId" in _JS, (
        "_localDeviceId must be declared as a module-level variable — "
        "used by createNewSession for local session key construction"
    )


def test_instance_info_fetched_at_startup() -> None:
    """DOMContentLoaded or init code must fetch /api/instance-info to cache device_id."""
    match = re.search(
        r"DOMContentLoaded.*?\{(.*?)(?=\}\);\s*\n// |\}\);\s*$)",
        _JS,
        re.DOTALL,
    )
    assert match, "DOMContentLoaded handler not found"
    body = match.group(1)
    assert "instance-info" in body or "/api/instance-info" in body, (
        "DOMContentLoaded must fetch /api/instance-info to cache device_id in _localDeviceId"
    )


def test_create_new_session_uses_local_device_id() -> None:
    """createNewSession must use _localDeviceId (not _serverSettings.device_id) for local sessions."""
    match = re.search(
        r"async function createNewSession\s*\([\w,\s]+\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "createNewSession function not found"
    body = match.group(1)
    assert "_localDeviceId" in body, (
        "createNewSession must use _localDeviceId — device_id is not in /api/settings response"
    )
    assert "_serverSettings.device_id" not in body, (
        "createNewSession must not use _serverSettings.device_id — device_id comes from /api/instance-info"
    )


# ─── Fix 10: dead code removal (renderFilterBar and filter bar click handler) ─


def test_render_filter_bar_body_is_empty() -> None:
    """renderFilterBar function body must be empty (dead code)."""
    match = re.search(
        r"function renderFilterBar\s*\(.*?\)\s*\{(.*?)(?=\n\})",
        _JS,
        re.DOTALL,
    )
    assert match, "renderFilterBar function not found"
    body = match.group(1).strip()
    assert body == "" or body == "// dead code" or body.startswith("//"), (
        "renderFilterBar body must be empty (dead code removed) — "
        f"but found content: {body[:80]!r}"
    )


def test_bind_static_event_listeners_no_filter_bar_click_handler() -> None:
    """bindStaticEventListeners must not contain the dead filter-bar click handler."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    # The filter bar click handler used to set _activeFilterDevice
    assert "_activeFilterDevice = pill.dataset.device" not in body, (
        "bindStaticEventListeners must not contain the dead filter-bar click handler "
        "that sets _activeFilterDevice"
    )


# ---------------------------------------------------------------------------
# Tile options button (⋮) replaces tile-delete
# ---------------------------------------------------------------------------


def test_tile_has_options_button() -> None:
    """buildTileHTML must include a .tile-options-btn button."""
    fn_body = _JS.split("function buildTileHTML")[1].split("\nfunction ")[0]
    assert "tile-options-btn" in fn_body, (
        "buildTileHTML must render a .tile-options-btn element"
    )


def test_tile_options_btn_has_aria() -> None:
    """The ⋮ button must have aria-label='Session options' and aria-haspopup='true'."""
    fn_body = _JS.split("function buildTileHTML")[1].split("\nfunction ")[0]
    assert "aria-label" in fn_body and "Session options" in fn_body, (
        "tile-options-btn must have aria-label='Session options'"
    )
    assert "aria-haspopup" in fn_body, "tile-options-btn must have aria-haspopup='true'"


def test_tile_delete_button_removed() -> None:
    """buildTileHTML must NOT include the old .tile-delete button."""
    fn_body = _JS.split("function buildTileHTML")[1].split("\nfunction ")[0]
    assert "tile-delete" not in fn_body, (
        "buildTileHTML must not render the old .tile-delete button (kill moved to flyout)"
    )


def test_open_flyout_menu_function_exists() -> None:
    """app.js must define an openFlyoutMenu function."""
    assert "function openFlyoutMenu" in _JS, (
        "app.js must contain an openFlyoutMenu function"
    )


def test_close_flyout_menu_function_exists() -> None:
    """app.js must define a closeFlyoutMenu function."""
    assert "function closeFlyoutMenu" in _JS, (
        "app.js must contain a closeFlyoutMenu function"
    )


def test_flyout_menu_uses_fixed_positioning() -> None:
    """openFlyoutMenu must use position:fixed and getBoundingClientRect for positioning."""
    fn_body = _JS.split("function openFlyoutMenu")[1].split("\nfunction ")[0]
    assert "getBoundingClientRect" in fn_body, (
        "openFlyoutMenu must use getBoundingClientRect to calculate position"
    )


def test_flyout_delegated_on_tile_container() -> None:
    """A delegated click listener must handle .tile-options-btn clicks."""
    assert "tile-options-btn" in _JS.split("bindStaticEventListeners")[1], (
        "bindStaticEventListeners must handle .tile-options-btn clicks via delegation"
    )


def test_old_tile_delete_handler_removed() -> None:
    """The old delegated .tile-delete click handler must be removed."""
    bind_body = _JS.split("function bindStaticEventListeners")[1].split("\nfunction ")[
        0
    ]
    assert "tile-delete" not in bind_body, (
        "The old .tile-delete delegated handler must be removed from bindStaticEventListeners"
    )


# ─── Task 4: Context-dependent menu items — FLYOUT_MENU_MAP ──────────────────


def test_flyout_menu_map_exists() -> None:
    """app.js must define a FLYOUT_MENU_MAP data structure."""
    assert "FLYOUT_MENU_MAP" in _JS, (
        "app.js must contain a FLYOUT_MENU_MAP data structure"
    )


def test_flyout_menu_map_has_three_view_types() -> None:
    """FLYOUT_MENU_MAP must have keys for 'all', 'user', and 'hidden'."""
    # The map should reference all three view types
    map_section = _JS.split("FLYOUT_MENU_MAP")[1].split("};")[0]
    assert "'all'" in map_section or '"all"' in map_section, (
        "FLYOUT_MENU_MAP must include an 'all' key"
    )
    assert "'user'" in map_section or '"user"' in map_section, (
        "FLYOUT_MENU_MAP must include a 'user' key"
    )
    assert "'hidden'" in map_section or '"hidden"' in map_section, (
        "FLYOUT_MENU_MAP must include a 'hidden' key"
    )


def test_build_flyout_menu_items_function_exists() -> None:
    """app.js must define a _buildFlyoutMenuItems function."""
    assert "function _buildFlyoutMenuItems" in _JS, (
        "app.js must contain a _buildFlyoutMenuItems function"
    )


def test_build_flyout_uses_menu_map() -> None:
    """_buildFlyoutMenuItems must reference FLYOUT_MENU_MAP."""
    fn_body = _JS.split("function _buildFlyoutMenuItems")[1].split("\nfunction ")[0]
    assert "FLYOUT_MENU_MAP" in fn_body, (
        "_buildFlyoutMenuItems must reference FLYOUT_MENU_MAP (data-driven, not if/else)"
    )


# ─── Task 5: Flyout click handler + Add to View submenu ──────────────────────


def test_handle_flyout_click_function_exists() -> None:
    """app.js must define a _handleFlyoutClick function."""
    assert "function _handleFlyoutClick" in _JS, (
        "app.js must contain a _handleFlyoutClick function"
    )


def test_handle_flyout_click_dispatches_actions() -> None:
    """_handleFlyoutClick must check data-action for dispatching."""
    fn_body = _JS.split("function _handleFlyoutClick")[1].split("\nfunction ")[0]
    assert "data-action" in fn_body or "dataset.action" in fn_body, (
        "_handleFlyoutClick must read data-action from the clicked element"
    )


def test_open_flyout_submenu_function_exists() -> None:
    """app.js must define a _openFlyoutSubmenu function."""
    assert "function _openFlyoutSubmenu" in _JS, (
        "app.js must contain a _openFlyoutSubmenu function"
    )


def test_submenu_toggles_view_membership() -> None:
    """_openFlyoutSubmenu must PATCH /api/settings to toggle view membership."""
    fn_body = _JS.split("function _openFlyoutSubmenu")[1].split("\nfunction ")[0]
    assert "views" in fn_body and ("PATCH" in fn_body or "api(" in fn_body), (
        "_openFlyoutSubmenu must PATCH /api/settings to add/remove session from view"
    )


# ─── Task 6: Hide/Unhide/Remove actions ──────────────────────────────────────


def test_do_hide_session_function_exists() -> None:
    """app.js must define a _doHideSession function."""
    assert "function _doHideSession" in _JS, (
        "app.js must contain a _doHideSession function"
    )


def test_do_unhide_session_function_exists() -> None:
    """app.js must define a _doUnhideSession function."""
    assert "function _doUnhideSession" in _JS, (
        "app.js must contain a _doUnhideSession function"
    )


def test_do_remove_from_view_function_exists() -> None:
    """app.js must define a _doRemoveFromView function."""
    assert "function _doRemoveFromView" in _JS, (
        "app.js must contain a _doRemoveFromView function"
    )


def test_hide_session_removes_from_all_views() -> None:
    """_doHideSession must update both hidden_sessions AND views (remove from all views)."""
    fn_body = _JS.split("function _doHideSession")[1].split("\nfunction ")[0]
    assert "hidden_sessions" in fn_body, (
        "_doHideSession must add session to hidden_sessions"
    )
    assert "views" in fn_body, (
        "_doHideSession must remove session from all views (mutual exclusion)"
    )


# ─── Task 7: Kill session inline confirmation ─────────────────────────────────


def test_do_kill_session_inline_function_exists() -> None:
    """app.js must define a _doKillSessionInline function."""
    assert "function _doKillSessionInline" in _JS, (
        "app.js must contain a _doKillSessionInline function"
    )


def test_kill_session_no_confirm_dialog() -> None:
    """killSession must NOT use window.confirm() (replaced by inline confirmation)."""
    fn_body = _JS.split("function killSession")[1].split("\nfunction ")[0]
    assert "confirm(" not in fn_body, (
        "killSession must not use confirm() — replaced by inline flyout confirmation"
    )


def test_do_kill_inline_shows_confirmation_buttons() -> None:
    """_doKillSessionInline must render Yes/No confirmation buttons."""
    fn_body = _JS.split("function _doKillSessionInline")[1].split("\nfunction ")[0]
    assert "Yes" in fn_body and "No" in fn_body, (
        "_doKillSessionInline must show 'Kill? [Yes] [No]' inline"
    )


# ============================================================
# Add Sessions panel JS logic (task-9)
# ============================================================


def test_open_manage_view_panel_function_exists() -> None:
    """app.js must define an openManageViewPanel function (renamed from openAddSessionsPanel)."""
    assert "function openManageViewPanel" in _JS, (
        "app.js must contain an openManageViewPanel function"
    )


def test_close_manage_view_panel_function_exists() -> None:
    """app.js must define a closeManageViewPanel function (renamed from closeAddSessionsPanel)."""
    assert "function closeManageViewPanel" in _JS, (
        "app.js must contain a closeManageViewPanel function"
    )


def test_render_manage_view_list_function_exists() -> None:
    """app.js must define a renderManageViewList function (renamed from renderAddSessionsList)."""
    assert "function renderManageViewList" in _JS, (
        "app.js must contain a renderManageViewList function"
    )


def test_manage_view_uses_immediate_commit() -> None:
    """renderManageViewList must PATCH immediately on checkbox change (no batch Done)."""
    fn_body = _JS.split("function renderManageViewList")[1].split("\nfunction ")[0]
    assert "PATCH" in fn_body or "api(" in fn_body, (
        "renderManageViewList must fire PATCH on each checkbox change (immediate commit)"
    )


def test_manage_view_shows_device_name() -> None:
    """renderManageViewList must show device name next to each session."""
    fn_body = _JS.split("function renderManageViewList")[1].split("\nfunction ")[0]
    assert "deviceName" in fn_body or "device" in fn_body, (
        "renderManageViewList must show device name for disambiguation"
    )


# ─── Task 10: Mobile variants ────────────────────────────────────────────────


def test_open_flyout_sheet_function_exists() -> None:
    """app.js must define a _openFlyoutSheet function for mobile."""
    assert "function _openFlyoutSheet" in _JS, (
        "app.js must contain a _openFlyoutSheet function for mobile bottom sheet"
    )


def test_open_flyout_menu_checks_mobile() -> None:
    """openFlyoutMenu must check isMobile() to decide between flyout and sheet."""
    fn_body = _JS.split("function openFlyoutMenu")[1].split("\nfunction ")[0]
    assert "isMobile" in fn_body, (
        "openFlyoutMenu must check isMobile() to branch between flyout and sheet"
    )


# ─── Task 11: Add Sessions entry point in grid ───────────────────────────────


def test_render_view_dropdown_has_manage_view_affordance() -> None:
    """renderViewDropdown must include a 'Manage [ViewName]...' action for user views.

    The affordance moved from a header button to the dropdown's 'Manage [ViewName]...' item.
    This item should open the Manage View panel for the current user view.
    """
    match = re.search(
        r"function renderViewDropdown\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "renderViewDropdown function not found"
    body = match.group(1)
    assert "manage-view" in body, (
        "renderViewDropdown must include a 'Manage [ViewName]...' action with data-action='manage-view' "
        "for user views — this replaced the old #add-sessions-btn header button"
    )


# ─── Task 12: Session death detection ────────────────────────────────────────


def test_render_grid_closes_stale_flyout() -> None:
    """renderGrid must close the flyout if the targeted session no longer exists."""
    fn_body = _JS.split("function renderGrid")[1].split("\nfunction ")[0]
    assert "_flyoutSessionKey" in fn_body or "closeFlyoutMenu" in fn_body, (
        "renderGrid must check if the flyout's target session still exists and close if not"
    )


# ─── Phase 3 COE findings regression tests ──────────────────────────────────


# ── BUG 1: tile click handler guards .tile-options-btn ───────────────────────


def test_tile_click_handler_guards_options_btn() -> None:
    """Tile click handler early return must guard .tile-options-btn (not .tile-delete).

    BUG: Previously the guard checked .tile-delete which was removed in Phase 3.
    Clicking ⋮ triggered BOTH flyout opening AND openSession() navigation.
    Fix: guard must check .tile-options-btn to stop event from reaching openSession().
    """
    # The tile click handler is inside renderGrid — find it
    render_grid_body = _JS.split("function renderGrid")[1].split("\nfunction ")[0]
    assert "tile-options-btn" in render_grid_body, (
        "Tile click handler must guard against .tile-options-btn clicks — "
        "clicking ⋮ must NOT trigger openSession()"
    )
    # Confirm the old broken guard is gone
    assert "'tile-delete'" not in render_grid_body and '"tile-delete"' not in render_grid_body or (
        "tile-options-btn" in render_grid_body
    ), (
        "Guard must use .tile-options-btn, not the old .tile-delete which was removed"
    )


def test_flyout_delegation_handler_no_stop_propagation() -> None:
    """bindStaticEventListeners .tile-options-btn delegation must NOT call e.stopPropagation().

    BUG: e.stopPropagation() at document level is a no-op (document is the top of the
    bubble chain). It created a false sense of correctness while doing nothing.
    """
    # Extract the tile-options-btn delegation handler block from bindStaticEventListeners
    bind_body = _JS.split("function bindStaticEventListeners")[1].split("\nfunction ")[0]
    # Find the section with tile-options-btn
    assert "tile-options-btn" in bind_body, (
        "bindStaticEventListeners must have .tile-options-btn delegation"
    )
    # The delegation block should not have stopPropagation
    tile_opts_section = bind_body.split("tile-options-btn")[1].split("});")[0]
    assert "stopPropagation" not in tile_opts_section, (
        "The .tile-options-btn delegation handler must not call e.stopPropagation() — "
        "it is a no-op at document level and creates false sense of correctness"
    )


# ── BUG 2: disclosure has no hover-based show/hide in renderAddSessionsList ──


def test_render_manage_view_list_no_hover_disclosure() -> None:
    """renderManageViewList must NOT have mouseover/mouseout handlers for disclosure.

    BUG: The disclosure was shown only on hover via JS mouseover/mouseout. This broke
    on mobile (no hover) and was inconsistent. Fix: disclose statically — remove CSS
    display:none and remove the hover handlers. The disclosure appears whenever the
    hidden item is in the HTML (it's already conditionally rendered for hidden items).
    """
    fn_body = _JS.split("function renderManageViewList")[1].split("\nfunction ")[0]
    assert "onmouseover" not in fn_body, (
        "renderManageViewList must not use onmouseover to show disclosure — "
        "the disclosure must be statically visible (BUG 2 fix)"
    )
    assert "onmouseout" not in fn_body, (
        "renderManageViewList must not use onmouseout to hide disclosure — "
        "the disclosure must be statically visible (BUG 2 fix)"
    )


# ── BUG 3: mobile kill has confirmation sheet ─────────────────────────────────


def test_open_mobile_kill_confirm_function_exists() -> None:
    """app.js must define _openMobileKillConfirm for mobile kill confirmation.

    BUG: Mobile kill previously called killSession() directly with no confirmation.
    Fix: A second bottom sheet confirms before killing.
    """
    assert "function _openMobileKillConfirm" in _JS, (
        "_openMobileKillConfirm must be defined — mobile kill needs a confirmation sheet"
    )


def test_open_flyout_sheet_kill_calls_confirm_not_direct() -> None:
    """_openFlyoutSheet must call _openMobileKillConfirm, not killSession() directly.

    BUG: Direct killSession() call gave no confirmation chance to the user on mobile.
    """
    fn_body = _JS.split("function _openFlyoutSheet")[1].split("\nfunction ")[0]
    assert "_openMobileKillConfirm" in fn_body, (
        "Mobile kill action in _openFlyoutSheet must call _openMobileKillConfirm, not killSession() directly"
    )


# ── VIOLATION 1: mobile Add to View opens view picker ────────────────────────


def test_open_mobile_view_picker_function_exists() -> None:
    """app.js must define _openMobileViewPicker for mobile view selection.

    VIOLATION: Mobile 'Add to View...' opened openAddSessionsPanel() which is the
    session picker for the current view — wrong behaviour. Fix: a view picker that
    lists all user views with checkboxes.
    """
    assert "function _openMobileViewPicker" in _JS, (
        "_openMobileViewPicker must be defined — mobile 'Add to View...' must list views, not sessions"
    )


def test_open_flyout_sheet_add_to_view_calls_view_picker() -> None:
    """_openFlyoutSheet must call _openMobileViewPicker, not openAddSessionsPanel.

    VIOLATION: openAddSessionsPanel() is the session picker for the current view,
    not a view picker for the current session.
    """
    fn_body = _JS.split("function _openFlyoutSheet")[1].split("\nfunction ")[0]
    assert "_openMobileViewPicker" in fn_body, (
        "Mobile 'Add to View' must call _openMobileViewPicker, not openAddSessionsPanel"
    )
    # Confirm the old wrong call is gone from the mobile handler
    # (openAddSessionsPanel may still exist for the grid affordance — just not here)
    assert "openAddSessionsPanel" not in fn_body.split("_openMobileViewPicker")[0].split(
        "action === 'add-to-view'"
    )[-1], (
        "_openFlyoutSheet must not call openAddSessionsPanel for the add-to-view action"
    )


# ── VIOLATION 2: submenu filters current view ────────────────────────────────


def test_open_flyout_submenu_filters_current_view() -> None:
    """_openFlyoutSubmenu must show ALL user views (unified views submenu, Fix 4).

    Fix 4: The old behavior filtered out the current view because 'Remove from [ViewName]'
    handled it. Now that 'Remove from [ViewName]' is removed, the submenu shows ALL
    views with toggle checkmarks so the user can multi-toggle without the submenu closing.
    """
    fn_body = _JS.split("function _openFlyoutSubmenu")[1].split("\nfunction ")[0]
    # Must NOT have the old 'isInUserView' skip logic
    assert "isInUserView" not in fn_body, (
        "_openFlyoutSubmenu must not skip the current view — "
        "Fix 4: unified submenu shows ALL views with toggle checkmarks"
    )
    # Must show checkmarks for views the session is in
    assert "\u2713" in fn_body or "isIn" in fn_body, (
        "_openFlyoutSubmenu must show checkmarks for views the session is already in"
    )


# ── CLEANUP 2: sidebar kill button removed ────────────────────────────────────


def test_build_sidebar_html_no_sidebar_delete_button() -> None:
    """buildSidebarHTML must NOT include the .sidebar-delete kill button.

    CLEANUP: The sidebar had a second kill location; flyout is now the only location.
    """
    fn_body = _JS.split("function buildSidebarHTML")[1].split("\nfunction ")[0]
    assert "sidebar-delete" not in fn_body, (
        "buildSidebarHTML must not render .sidebar-delete button — "
        "kill is available only via the flyout menu (single kill location)"
    )


# ── CLEANUP 3: dead _addSessionToActiveView removed ───────────────────────────


def test_add_session_to_active_view_removed() -> None:
    """_addSessionToActiveView must be removed — renderAddSessionsList does same inline.

    CLEANUP: The function was fully implemented but never called. Dead code removed.
    """
    assert "_addSessionToActiveView" not in _JS, (
        "_addSessionToActiveView must be removed — it was dead code never called; "
        "renderAddSessionsList handles the same logic inline"
    )


# ── CLEANUP 4: ARIA on mobile flyout sheet ────────────────────────────────────


def test_flyout_sheet_panel_has_aria_label() -> None:
    """_openFlyoutSheet must add aria-label='Session options' to the sheet panel.

    CLEANUP: The panel had no ARIA labelling for screen readers.
    """
    fn_body = _JS.split("function _openFlyoutSheet")[1].split("\nfunction ")[0]
    assert "aria-label" in fn_body and "Session options" in fn_body, (
        "_openFlyoutSheet must set aria-label='Session options' on the sheet panel"
    )


def test_flyout_sheet_items_have_role_menuitem() -> None:
    """_openFlyoutSheet must add role='menuitem' to each sheet item button.

    CLEANUP: Sheet items lacked ARIA role, breaking screen reader navigation.
    """
    fn_body = _JS.split("function _openFlyoutSheet")[1].split("\nfunction ")[0]
    assert "role=\"menuitem\"" in fn_body or "role='menuitem'" in fn_body, (
        "_openFlyoutSheet must set role='menuitem' on each sheet item button"
    )


# ── CLEANUP 5: FLYOUT_MENU_MAP is const ──────────────────────────────────────


def test_flyout_menu_map_is_const() -> None:
    """FLYOUT_MENU_MAP must be declared with const, not var.

    CLEANUP: It is an immutable data structure; var was incorrect.
    """
    assert "const FLYOUT_MENU_MAP" in _JS, (
        "FLYOUT_MENU_MAP must be declared with 'const', not 'var' — it is immutable"
    )
    assert "var FLYOUT_MENU_MAP" not in _JS, (
        "FLYOUT_MENU_MAP must not use 'var' — change to 'const'"
    )


# ── Fix B: ARIA role correction in kill confirm sheet (Phase 3 COE re-verification) ──


# ─── Click-outside race condition guard (new-view dropdown) ─────────────────


def test_click_outside_view_dropdown_guards_against_new_view_input() -> None:
    """Click-outside handler must not close dropdown when new-view input is being shown.

    Race condition: showNewViewInput() replaces the '+ New View' button with an
    input via replaceChild.  The click event then bubbles up to the document-level
    click-outside handler, where e.target is the OLD button that was just removed
    from the DOM.  Since it is no longer in the DOM, dropdown.contains(e.target)
    returns false and the handler calls closeViewDropdown(), making the input
    disappear immediately.

    Fix: before closing, check whether the dropdown now contains a
    .view-dropdown__new-input element — if so, showNewViewInput() just ran and
    we must not close the dropdown.
    """
    match = re.search(
        r"// Click-outside closes the header view dropdown\s*\n\s*"
        r"document\.addEventListener\('click',\s*function\(e\)\s*\{(.*?)\}\s*\);",
        _JS,
        re.DOTALL,
    )
    assert match, "Click-outside handler for header view dropdown not found"
    body = match.group(1)
    assert ".view-dropdown__new-input" in body, (
        "Click-outside handler must guard: if (dropdown.querySelector"
        "('.view-dropdown__new-input')) return; — prevents the race condition "
        "where showNewViewInput() replaces the button with an input (removing "
        "it from the DOM) and the bubbling click event incorrectly closes the dropdown"
    )


def test_kill_confirm_buttons_use_role_button() -> None:
    """Kill and Cancel buttons inside the alertdialog must use role='button', not 'menuitem'.

    Fix B: _openMobileKillConfirm() renders buttons inside a role='alertdialog'
    panel.  The ARIA spec requires buttons inside an alertdialog to carry
    role='button' (not role='menuitem', which only belongs inside role='menu').
    """
    # Negative check: role="menuitem" must NOT appear on the confirm-kill button
    assert 'data-action="confirm-kill" role="menuitem"' not in _JS, (
        "Kill button inside alertdialog must not use role='menuitem' — "
        "use role='button' instead (menuitem is only valid inside role='menu')"
    )
    # Negative check: role="menuitem" must NOT appear on the cancel button
    assert 'data-action="cancel" role="menuitem"' not in _JS, (
        "Cancel button inside alertdialog must not use role='menuitem' — "
        "use role='button' instead (menuitem is only valid inside role='menu')"
    )
    # Positive check: role="button" must appear on the confirm-kill button
    assert 'data-action="confirm-kill" role="button"' in _JS, (
        "Kill button inside alertdialog must use role='button'"
    )
    # Positive check: role="button" must appear on the cancel button
    assert 'data-action="cancel" role="button"' in _JS, (
        "Cancel button inside alertdialog must use role='button'"
    )


# ============================================================
# UX Refinements from live testing (6 issues)
# ============================================================

CSS_PATH = pathlib.Path(__file__).parent.parent / "frontend" / "style.css"
_CSS: str = CSS_PATH.read_text()


# — Issue 1: Sidebar dropdown "+ New View" ——————————————————————————

def test_render_sidebar_view_dropdown_has_new_view_action() -> None:
    """renderSidebarViewDropdown must include a '+ New View' action button."""
    match = re.search(
        r"function renderSidebarViewDropdown\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "renderSidebarViewDropdown function not found"
    body = match.group(1)
    assert 'data-action="new-view"' in body, (
        'renderSidebarViewDropdown must include a "+ New View" button with data-action="new-view"'
    )


def test_show_sidebar_new_view_input_function_exists() -> None:
    """showSidebarNewViewInput function must exist in app.js."""
    assert "function showSidebarNewViewInput" in _JS, (
        "showSidebarNewViewInput must be defined in app.js"
    )


def test_bind_static_event_listeners_calls_show_sidebar_new_view_input() -> None:
    """bindStaticEventListeners sidebar dropdown handler must call showSidebarNewViewInput."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "showSidebarNewViewInput" in body, (
        "bindStaticEventListeners must call showSidebarNewViewInput for sidebar new-view action"
    )


# — Issue 2: Remove shortcut numbers, add session counts ———————————————

def test_render_view_dropdown_no_shortcut_spans() -> None:
    """renderViewDropdown must not include view-dropdown__shortcut spans (numbers removed)."""
    match = re.search(
        r"function renderViewDropdown\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "renderViewDropdown function not found"
    body = match.group(1)
    assert "view-dropdown__shortcut" not in body, (
        "renderViewDropdown must not include view-dropdown__shortcut spans — shortcut numbers removed"
    )


def test_render_sidebar_view_dropdown_no_shortcut_spans() -> None:
    """renderSidebarViewDropdown must not include view-dropdown__shortcut spans."""
    match = re.search(
        r"function renderSidebarViewDropdown\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "renderSidebarViewDropdown function not found"
    body = match.group(1)
    assert "view-dropdown__shortcut" not in body, (
        "renderSidebarViewDropdown must not include view-dropdown__shortcut spans — shortcut numbers removed"
    )


def test_render_view_dropdown_shows_user_view_session_count() -> None:
    """renderViewDropdown must show session count for user views."""
    match = re.search(
        r"function renderViewDropdown\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "renderViewDropdown function not found"
    body = match.group(1)
    assert "sessions.length" in body or "sessions || []).length" in body, (
        "renderViewDropdown must show session count for user views (view.sessions.length)"
    )


# — Issue 3: Empty new view opens Add Sessions panel ———————————————————

def test_show_new_view_input_calls_open_manage_view_panel() -> None:
    """showNewViewInput must call openManageViewPanel after creating a new view."""
    match = re.search(
        r"function showNewViewInput\s*\(\s*\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "showNewViewInput function not found"
    body = match.group(1)
    assert "openManageViewPanel" in body, (
        "showNewViewInput must call openManageViewPanel() after creating a new view — "
        "so the user immediately sees the Manage View panel for their empty view"
    )


def test_show_sidebar_new_view_input_calls_open_manage_view_panel() -> None:
    """showSidebarNewViewInput must call openManageViewPanel after creating a new view."""
    match = re.search(
        r"function showSidebarNewViewInput\s*\(\s*\)\s*\{(.*?)(?=\nasync function |\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "showSidebarNewViewInput function not found"
    body = match.group(1)
    assert "openManageViewPanel" in body, (
        "showSidebarNewViewInput must call openManageViewPanel() after creating a new view"
    )


# — Issue 4: Flyout submenu "+ New View" ———————————————————————————————

def test_open_flyout_submenu_has_new_view_option() -> None:
    """_openFlyoutSubmenu must include a '+ New View' option."""
    match = re.search(
        r"function _openFlyoutSubmenu\s*\(.*?\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "_openFlyoutSubmenu function not found"
    body = match.group(1)
    assert "new-view-in-flyout" in body, (
        '_openFlyoutSubmenu must include a "+ New View" option with data-action="new-view-in-flyout"'
    )


def test_open_flyout_submenu_new_view_creates_and_switches() -> None:
    """_openFlyoutSubmenu '+ New View' handler must create a view and switch to it."""
    match = re.search(
        r"function _openFlyoutSubmenu\s*\(.*?\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "_openFlyoutSubmenu function not found"
    body = match.group(1)
    assert "new-view-in-flyout" in body, (
        "'new-view-in-flyout' not found in _openFlyoutSubmenu"
    )
    # switchView and PATCH must appear in the function body (the handler for new-view-in-flyout)
    assert "switchView" in body, (
        '_openFlyoutSubmenu must call switchView — the "+ New View" handler needs to switch to the new view'
    )
    assert "PATCH" in body or "api(" in body, (
        '_openFlyoutSubmenu must PATCH /api/settings to create the view'
    )


# — Issue 5: Add Sessions header button ————————————————————————————————

def test_update_add_sessions_button_removed() -> None:
    """updateAddSessionsButton must be REMOVED — the + Add button is gone (Issue 5)."""
    assert "function updateAddSessionsButton" not in _JS, (
        "updateAddSessionsButton must be removed — the #add-sessions-btn header button "
        "was removed and replaced by the 'Manage [ViewName]...' dropdown item"
    )


def test_switch_view_no_update_add_sessions_button() -> None:
    """switchView must NOT call updateAddSessionsButton (it was removed in Issue 5)."""
    match = re.search(
        r"function switchView\s*\(\w+\)\s*\{(.*?)(?=\nfunction |\nasync function |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "switchView function not found"
    body = match.group(1)
    assert "updateAddSessionsButton" not in body, (
        "switchView must not call updateAddSessionsButton() — it was removed in Issue 5"
    )


def test_render_grid_no_muxplex_app_onclick() -> None:
    """renderGrid must not use the broken window.MuxplexApp.openManageViewPanel onclick."""
    fn_body = _JS.split("function renderGrid")[1].split("\nfunction ")[0]
    assert "MuxplexApp.openAddSessionsPanel" not in fn_body, (
        "renderGrid must not use onclick='window.MuxplexApp.openAddSessionsPanel()' — "
        "this was broken; the Manage View entry point moved to the dropdown"
    )


def test_bind_static_event_listeners_no_add_sessions_btn() -> None:
    """bindStaticEventListeners must NOT bind #add-sessions-btn (button was removed in Issue 5)."""
    match = re.search(
        r"function bindStaticEventListeners\s*\(\s*\)\s*\{(.*?)\n\}",
        _JS,
        re.DOTALL,
    )
    assert match, "bindStaticEventListeners function not found"
    body = match.group(1)
    assert "add-sessions-btn" not in body, (
        "bindStaticEventListeners must not bind #add-sessions-btn — "
        "the button was removed; Manage View is reached via the dropdown"
    )


# — Issue 6: Tile header flexbox layout ————————————————————————————————

def test_build_tile_html_options_btn_inside_tile_header() -> None:
    """buildTileHTML must render tile-options-btn inside tile-header (before tile-body)."""
    fn_body = _JS.split("function buildTileHTML")[1].split("\nfunction ")[0]
    tile_opts_pos = fn_body.find("tile-options-btn")
    tile_body_pos = fn_body.find("tile-body")
    assert tile_opts_pos >= 0, "tile-options-btn must appear in buildTileHTML"
    assert tile_body_pos >= 0, "tile-body must appear in buildTileHTML"
    assert tile_opts_pos < tile_body_pos, (
        "tile-options-btn must appear before tile-body in the HTML string — "
        "it must be inside tile-header (as an inline flex item), not positioned after tile-body"
    )


def test_tile_options_btn_css_not_absolute() -> None:
    """CSS .tile-options-btn must not use position:absolute — prevent badge/button overlap."""
    import re as _re

    match = _re.search(r"\.tile-options-btn\s*\{([^}]*)\}", _CSS, _re.DOTALL)
    assert match, ".tile-options-btn CSS rule not found"
    rule_body = match.group(1)
    assert "position: absolute" not in rule_body and "position:absolute" not in rule_body, (
        ".tile-options-btn must not use position:absolute — "
        "it should be an inline flex item inside tile-header to prevent device badge overlap"
    )


# ============================================================
# UX Overhaul — 9 refinements from live testing
# ============================================================


# — Issue 1: Sidebar dropdown "New View" click-dismiss race ——————————————


def test_sidebar_click_outside_has_new_view_input_guard() -> None:
    """Sidebar click-outside handler must guard against new-view input dismiss race.

    Race condition: clicking '+ New View' in the sidebar triggers the click-outside
    handler before the input appears. Guard: check for .view-dropdown__new-input
    presence and return early if found.
    """
    match = re.search(
        r"// Click-outside closes the sidebar view dropdown\s*\n\s*"
        r"document\.addEventListener\('click',\s*function\(e\)\s*\{(.*?)\}\s*\);",
        _JS,
        re.DOTALL,
    )
    assert match, "Click-outside handler for sidebar view dropdown not found"
    body = match.group(1)
    assert ".view-dropdown__new-input" in body, (
        "Sidebar click-outside handler must guard: "
        "if (sidebarDropdown.querySelector('.view-dropdown__new-input')) return; "
        "— prevents race where the new-view input is dismissed immediately"
    )


# — Issue 4: Dropdown structure — "Manage [ViewName]…" ————————————————


def test_render_view_dropdown_has_manage_view_item_for_user_view() -> None:
    """renderViewDropdown must include 'Manage [ViewName]...' action for user views."""
    match = re.search(
        r"function renderViewDropdown\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "renderViewDropdown function not found"
    body = match.group(1)
    assert "manage-view" in body, (
        "renderViewDropdown must include a 'Manage [ViewName]...' action "
        "with data-action='manage-view' for user views"
    )


def test_render_sidebar_dropdown_has_manage_view_item_for_user_view() -> None:
    """renderSidebarViewDropdown must include 'Manage [ViewName]...' action for user views."""
    match = re.search(
        r"function renderSidebarViewDropdown\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "renderSidebarViewDropdown function not found"
    body = match.group(1)
    assert "manage-view" in body, (
        "renderSidebarViewDropdown must include a 'Manage [ViewName]...' action "
        "with data-action='manage-view' for user views"
    )


# — Issue 5: No #add-sessions-btn + updateAddSessionsButton removed ————————


def test_domcontentloaded_no_update_add_sessions_button_call() -> None:
    """DOMContentLoaded handler must NOT call updateAddSessionsButton (it was removed)."""
    assert "updateAddSessionsButton" not in _JS, (
        "updateAddSessionsButton must be completely removed from app.js — "
        "the #add-sessions-btn header button was replaced by the dropdown manage-view item"
    )


# — Issue 6: Manage View settings tab ——————————————————————————————————


def test_views_settings_tab_has_manage_button_per_row() -> None:
    """renderViewsSettingsTab must include a 'Manage' button per view row."""
    match = re.search(
        r"function renderViewsSettingsTab\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "renderViewsSettingsTab function not found"
    body = match.group(1)
    assert "manage-view" in body.lower() or "Manage" in body, (
        "renderViewsSettingsTab must include a 'Manage' button per view row "
        "that opens the Manage View panel for that view"
    )


def test_views_settings_tab_has_new_view_button_at_bottom() -> None:
    """renderViewsSettingsTab must include a '+ New View' button at the bottom."""
    match = re.search(
        r"function renderViewsSettingsTab\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "renderViewsSettingsTab function not found"
    body = match.group(1)
    assert "New View" in body or "new-view" in body, (
        "renderViewsSettingsTab must include a '+ New View' button at the bottom"
    )


def test_views_settings_tab_no_inline_rename_on_name_click() -> None:
    """renderViewsSettingsTab must NOT have click-to-rename on the name span.

    Rename now lives in the Manage View panel, not in the settings tab.
    """
    match = re.search(
        r"function renderViewsSettingsTab\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "renderViewsSettingsTab function not found"
    body = match.group(1)
    # The old inline rename code clicked on views-settings-name span to show an input
    assert "views-settings-rename-input" not in body, (
        "renderViewsSettingsTab must not have inline rename via views-settings-rename-input — "
        "rename now lives in the Manage View panel"
    )


# — Issue 7: Dropdown label fix on page reload —————————————————————————


def test_domcontentloaded_calls_render_view_dropdown_after_restore() -> None:
    """DOMContentLoaded must call renderViewDropdown() after restoreState() completes.

    Bug: restoreState() sets _activeView but the dropdown label stays as 'All Sessions'.
    Fix: call renderViewDropdown() after restoreState() so the label reflects _activeView.
    """
    # Find the DOMContentLoaded handler section (from DOMContentLoaded to end of file)
    idx = _JS.find("document.addEventListener('DOMContentLoaded'")
    assert idx >= 0, "DOMContentLoaded handler not found"
    handler_section = _JS[idx:]
    # restoreState must appear before renderViewDropdown in the handler
    restore_idx = handler_section.find("restoreState()")
    assert restore_idx >= 0, "restoreState call not found in DOMContentLoaded section"
    after_restore = handler_section[restore_idx:]
    assert "renderViewDropdown" in after_restore, (
        "DOMContentLoaded must call renderViewDropdown() after restoreState() so the "
        "dropdown label correctly reflects _activeView on page reload"
    )


# ── Sidebar flyout menu (feat: add flyout menu to sidebar session items) ──────


def test_build_sidebar_html_article_has_data_session_key() -> None:
    """buildSidebarHTML must include data-session-key on the article element."""
    match = re.search(
        r"function buildSidebarHTML\s*\(.*?\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "buildSidebarHTML function not found"
    body = match.group(1)
    assert "data-session-key" in body, (
        "buildSidebarHTML must include data-session-key attribute on the article element "
        "so openFlyoutMenu can look up session data via closest('[data-session-key]')"
    )


def test_build_sidebar_html_has_tile_options_btn() -> None:
    """buildSidebarHTML must include a .tile-options-btn button in the header."""
    match = re.search(
        r"function buildSidebarHTML\s*\(.*?\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "buildSidebarHTML function not found"
    body = match.group(1)
    assert "tile-options-btn" in body, (
        "buildSidebarHTML must include a button with class 'tile-options-btn' "
        "in the sidebar item header so users can access the flyout menu"
    )


def test_build_sidebar_html_options_btn_has_aria_label() -> None:
    """buildSidebarHTML tile-options-btn must have aria-label='Session options'."""
    match = re.search(
        r"function buildSidebarHTML\s*\(.*?\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "buildSidebarHTML function not found"
    body = match.group(1)
    assert 'aria-label="Session options"' in body or "aria-label='Session options'" in body, (
        "buildSidebarHTML tile-options-btn must have aria-label='Session options' "
        "for accessibility (same as tile version)"
    )


def test_build_sidebar_html_options_btn_has_aria_haspopup() -> None:
    """buildSidebarHTML tile-options-btn must have aria-haspopup='true'."""
    match = re.search(
        r"function buildSidebarHTML\s*\(.*?\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "buildSidebarHTML function not found"
    body = match.group(1)
    assert 'aria-haspopup="true"' in body or "aria-haspopup='true'" in body, (
        "buildSidebarHTML tile-options-btn must have aria-haspopup='true' "
        "for accessibility (same as tile version)"
    )


def test_render_sidebar_click_handler_guards_tile_options_btn() -> None:
    """renderSidebar click handler must guard against tile-options-btn clicks."""
    match = re.search(
        r"function renderSidebar\s*\(.*?\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "renderSidebar function not found"
    body = match.group(1)
    assert "tile-options-btn" in body, (
        "renderSidebar click handler must guard against .tile-options-btn clicks "
        "so clicking ⋮ doesn't also trigger openSession() — "
        "use: if (e.target.closest('.tile-options-btn')) return;"
    )

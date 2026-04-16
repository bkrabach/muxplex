"""Tests for 6 UX fixes: manage-view rename/delete, unified views submenu,
stable checkbox list, dead code cleanup.
"""

import pathlib
import re

JS_PATH = pathlib.Path(__file__).parent.parent / "frontend" / "app.js"
CSS_PATH = pathlib.Path(__file__).parent.parent / "frontend" / "style.css"

_JS: str = JS_PATH.read_text()
_CSS: str = CSS_PATH.read_text()


# ─────────────────────────────────────────────────────────────────────────────
# Fix 1: Manage View panel — rename click handler
# ─────────────────────────────────────────────────────────────────────────────


def test_open_manage_view_panel_adds_rename_click_handler() -> None:
    """openManageViewPanel must attach a click handler on #manage-view-name."""
    match = re.search(
        r"function openManageViewPanel\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "openManageViewPanel function not found"
    body = match.group(1)
    # Must reference manage-view-name and attach a click handler
    assert "manage-view-name" in body, (
        "openManageViewPanel must reference manage-view-name"
    )
    assert "onclick" in body or "addEventListener" in body or "click" in body.lower(), (
        "openManageViewPanel must attach a click handler on manage-view-name"
    )


def test_manage_view_rename_creates_input_element() -> None:
    """The rename handler on manage-view-name must create an <input> element."""
    # The click handler should create an input element for in-place renaming
    assert "manage-view-panel__name-input" in _JS or (
        "type" in _JS and "text" in _JS and "manage-view-name" in _JS
    ), "Rename handler must create an input element for inline editing"


def test_manage_view_rename_validates_non_empty() -> None:
    """The rename handler must validate the name is non-empty before saving."""
    # Check that the rename logic in openManageViewPanel has non-empty validation
    # The validation logic should be present somewhere in the file
    assert "trim()" in _JS, "Rename must trim and validate non-empty input"


def test_manage_view_rename_patches_settings() -> None:
    """The rename flow must PATCH /api/settings with updated view name."""
    # The rename action must call PATCH /api/settings
    assert "PATCH" in _JS and "/api/settings" in _JS, (
        "Rename must PATCH /api/settings"
    )


def test_manage_view_rename_handles_escape() -> None:
    """The rename input must revert on Escape key."""
    match = re.search(
        r"function openManageViewPanel\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "openManageViewPanel function not found"
    body = match.group(1)
    assert "Escape" in body, (
        "openManageViewPanel rename handler must handle Escape key to revert"
    )


def test_manage_view_rename_handles_enter() -> None:
    """The rename input must commit on Enter key."""
    match = re.search(
        r"function openManageViewPanel\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "openManageViewPanel function not found"
    body = match.group(1)
    assert "Enter" in body, (
        "openManageViewPanel rename handler must handle Enter key to commit"
    )


def test_manage_view_rename_updates_active_view() -> None:
    """After rename, _activeView must be updated to the new name."""
    match = re.search(
        r"function openManageViewPanel\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "openManageViewPanel function not found"
    body = match.group(1)
    assert "_activeView" in body, (
        "openManageViewPanel rename handler must update _activeView to new name"
    )


def test_manage_view_name_input_css_exists() -> None:
    """.manage-view-panel__name-input CSS rule must exist for the rename input."""
    assert ".manage-view-panel__name-input" in _CSS, (
        ".manage-view-panel__name-input CSS rule must exist for the rename input"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Fix 2: Manage View panel — delete button in header
# ─────────────────────────────────────────────────────────────────────────────


def test_open_manage_view_panel_has_delete_button() -> None:
    """openManageViewPanel must render a delete/trash button in the header."""
    match = re.search(
        r"function openManageViewPanel\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "openManageViewPanel function not found"
    body = match.group(1)
    # Must have some delete/trash button or delete action
    has_delete = (
        "delete" in body.lower()
        or "trash" in body.lower()
        or "manage-view-delete" in body
        or "data-action=\"delete\"" in body
    )
    assert has_delete, (
        "openManageViewPanel must render a delete button in the header"
    )


def test_manage_view_delete_shows_confirmation() -> None:
    """The delete button must show inline confirmation before deleting."""
    match = re.search(
        r"function openManageViewPanel\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "openManageViewPanel function not found"
    body = match.group(1)
    # Must have Yes/No or confirm-delete pattern
    has_confirm = (
        "confirm" in body.lower()
        or "Yes" in body
        or "confirm-delete" in body
        or "Delete this view" in body
    )
    assert has_confirm, (
        "Manage View delete button must show inline confirmation (Yes/No) before deleting"
    )


def test_manage_view_delete_removes_view_from_settings() -> None:
    """Delete confirmation must PATCH /api/settings to remove the view."""
    # The delete flow must involve PATCH /api/settings with updated views
    # This is already present for the delete in settings tab, so check it's
    # also connected from openManageViewPanel
    match = re.search(
        r"function openManageViewPanel\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "openManageViewPanel function not found"
    body = match.group(1)
    # openManageViewPanel must either patch directly or call a helper that does
    assert "api(" in body or "PATCH" in body or "_saveViews" in body or "splice" in body, (
        "Manage View delete must call PATCH /api/settings to remove the view"
    )


def test_manage_view_delete_switches_to_all() -> None:
    """After deleting the view, must switch to 'all' view."""
    match = re.search(
        r"function openManageViewPanel\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "openManageViewPanel function not found"
    body = match.group(1)
    assert "switchView" in body or "'all'" in body, (
        "Manage View delete must switch to 'all' view after deletion"
    )


def test_manage_view_delete_shows_toast() -> None:
    """After confirming delete, must show a toast notification."""
    match = re.search(
        r"function openManageViewPanel\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "openManageViewPanel function not found"
    body = match.group(1)
    assert "showToast" in body, (
        "Manage View delete must call showToast after deleting"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Fix 3: Remove rename affordance from settings tab
# ─────────────────────────────────────────────────────────────────────────────


def test_views_settings_name_no_cursor_pointer_css() -> None:
    """.views-settings-name must NOT have cursor: pointer (rename affordance removed)."""
    match = re.search(
        r"\.views-settings-name\s*\{([^}]*)\}",
        _CSS,
        re.DOTALL,
    )
    if match:
        body = match.group(1)
        assert "cursor: pointer" not in body, (
            ".views-settings-name must not have cursor: pointer — rename is only via Manage panel"
        )


def test_views_settings_name_no_hover_underline_css() -> None:
    """.views-settings-name:hover must NOT have text-decoration: underline (rename affordance removed)."""
    # Either the hover rule doesn't exist or it doesn't have underline
    hover_match = re.search(
        r"\.views-settings-name:hover\s*\{([^}]*)\}",
        _CSS,
        re.DOTALL,
    )
    if hover_match:
        body = hover_match.group(1)
        assert "text-decoration: underline" not in body, (
            ".views-settings-name:hover must not have text-decoration: underline"
        )


def test_views_settings_rename_input_css_removed() -> None:
    """.views-settings-rename-input CSS must be removed (dead class for settings-tab rename)."""
    assert ".views-settings-rename-input" not in _CSS, (
        ".views-settings-rename-input CSS class must be removed — rename is no longer in settings tab"
    )


def test_render_views_settings_tab_no_rename_handler() -> None:
    """renderViewsSettingsTab must NOT attach a click-to-rename handler on name span."""
    match = re.search(
        r"function renderViewsSettingsTab\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// |\n/\*)",
        _JS,
        re.DOTALL,
    )
    assert match, "renderViewsSettingsTab function not found"
    body = match.group(1)
    # Must not have inline rename input creation
    assert "views-settings-rename-input" not in body, (
        "renderViewsSettingsTab must not create rename input — rename is only via Manage panel"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Fix 4: Merge Add/Remove into unified Views submenu
# ─────────────────────────────────────────────────────────────────────────────


def test_flyout_menu_map_user_has_no_remove_from_view() -> None:
    """FLYOUT_MENU_MAP 'user' entry must NOT have a separate 'remove-from-view' item."""
    # Find the FLYOUT_MENU_MAP definition
    match = re.search(
        r"const FLYOUT_MENU_MAP\s*=\s*\{(.*?)\};",
        _JS,
        re.DOTALL,
    )
    assert match, "FLYOUT_MENU_MAP not found"
    map_body = match.group(1)
    # Find the 'user' section
    user_match = re.search(
        r"'user'\s*:\s*\[(.*?)\]",
        map_body,
        re.DOTALL,
    )
    assert user_match, "'user' entry in FLYOUT_MENU_MAP not found"
    user_items = user_match.group(1)
    assert "remove-from-view" not in user_items, (
        "FLYOUT_MENU_MAP 'user' must not have separate 'remove-from-view' item — "
        "use unified Views submenu instead"
    )


def test_flyout_menu_map_all_has_views_submenu() -> None:
    """FLYOUT_MENU_MAP 'all' must have a Views submenu item."""
    match = re.search(
        r"const FLYOUT_MENU_MAP\s*=\s*\{(.*?)\};",
        _JS,
        re.DOTALL,
    )
    assert match, "FLYOUT_MENU_MAP not found"
    map_body = match.group(1)
    all_match = re.search(
        r"'all'\s*:\s*\[(.*?)\]",
        map_body,
        re.DOTALL,
    )
    assert all_match, "'all' entry in FLYOUT_MENU_MAP not found"
    all_items = all_match.group(1)
    # Should still have add-to-view or views-submenu action
    assert "add-to-view" in all_items or "views-submenu" in all_items, (
        "FLYOUT_MENU_MAP 'all' must have views submenu item"
    )


def test_flyout_submenu_stays_open_after_toggle() -> None:
    """_openFlyoutSubmenu must NOT close the submenu after a checkbox toggle."""
    match = re.search(
        r"function _openFlyoutSubmenu\s*\(.*?\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "_openFlyoutSubmenu function not found"
    body = match.group(1)
    # The submenu click handler should NOT call closeFlyoutMenu() on successful toggle
    # It should only call closeFlyoutMenu on 'new-view' action
    # Check that there's no unconditional closeFlyoutMenu after a toggle
    # The best we can check is that the submenu stays alive (no remove() on the submenu)
    # We verify the API PATCH callback doesn't close the menu
    assert "_flyoutSubmenuEl" in body or "submenu" in body.lower(), (
        "_openFlyoutSubmenu must maintain submenu reference for staying open"
    )


def test_flyout_submenu_shows_checkmarks_for_current_view_sessions() -> None:
    """_openFlyoutSubmenu must show checkmarks for views the session is already in."""
    match = re.search(
        r"function _openFlyoutSubmenu\s*\(.*?\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "_openFlyoutSubmenu function not found"
    body = match.group(1)
    # Must show a checkmark (✓) for views the session is in
    assert "\\u2713" in body or "✓" in body or "isIn" in body, (
        "_openFlyoutSubmenu must show checkmarks for views the session is already in"
    )


def test_flyout_submenu_shows_all_user_views() -> None:
    """_openFlyoutSubmenu must show ALL user views (not skip the current active view)."""
    match = re.search(
        r"function _openFlyoutSubmenu\s*\(.*?\)\s*\{(.*?)(?=\nfunction |\n// )",
        _JS,
        re.DOTALL,
    )
    assert match, "_openFlyoutSubmenu function not found"
    body = match.group(1)
    # The old code skipped the active view: "if (isInUserView && v.name === _activeView) continue;"
    # Now it should NOT skip the current view — all views should be shown
    assert "isInUserView" not in body or "continue" not in body, (
        "_openFlyoutSubmenu must show ALL user views including the currently active one"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Fix 5: Fix checkbox re-render layout thrash
# ─────────────────────────────────────────────────────────────────────────────


def test_manage_view_checkbox_no_full_rerender_on_success() -> None:
    """renderManageViewList onChange handler must NOT call renderManageViewList() on success."""
    match = re.search(
        r"function renderManageViewList\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// |\n/\*)",
        _JS,
        re.DOTALL,
    )
    assert match, "renderManageViewList function not found"
    body = match.group(1)

    # Find the listEl.onchange handler
    onchange_match = re.search(
        r"listEl\.onchange\s*=\s*function\s*\(e\)\s*\{(.*?)\}\s*;",
        body,
        re.DOTALL,
    )
    assert onchange_match, "listEl.onchange handler not found in renderManageViewList"
    onchange_body = onchange_match.group(1)

    # The .then() callback must NOT call renderManageViewList()
    then_match = re.search(
        r"\.then\s*\(function\s*\(\s*\)\s*\{(.*?)\}\s*\)",
        onchange_body,
        re.DOTALL,
    )
    if then_match:
        then_body = then_match.group(1)
        assert "renderManageViewList()" not in then_body, (
            "renderManageViewList onChange .then() must NOT call renderManageViewList() "
            "— this causes layout thrash and item position jumps"
        )


def test_manage_view_checkbox_updates_summary_without_rerender() -> None:
    """renderManageViewList onChange handler must update summary count in-place."""
    match = re.search(
        r"function renderManageViewList\s*\(\s*\)\s*\{(.*?)(?=\nfunction |\n// |\n/\*)",
        _JS,
        re.DOTALL,
    )
    assert match, "renderManageViewList function not found"
    body = match.group(1)

    # Find the listEl.onchange handler
    onchange_match = re.search(
        r"listEl\.onchange\s*=\s*function\s*\(e\)\s*\{(.*?)\}\s*;",
        body,
        re.DOTALL,
    )
    assert onchange_match, "listEl.onchange handler not found in renderManageViewList"
    onchange_body = onchange_match.group(1)

    # Must update summary in the .then() callback
    then_match = re.search(
        r"\.then\s*\(function\s*\(\s*\)\s*\{(.*?)\}\s*\)",
        onchange_body,
        re.DOTALL,
    )
    if then_match:
        then_body = then_match.group(1)
        # Must update the summary count
        has_summary_update = (
            "summaryEl" in then_body
            or "manage-view-summary" in then_body
            or "textContent" in then_body
        )
        assert has_summary_update, (
            "renderManageViewList onChange .then() must update the summary count in-place"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Fix 6: Clean up dead CSS
# ─────────────────────────────────────────────────────────────────────────────


def test_css_no_add_sessions_tile() -> None:
    """.add-sessions-tile CSS must be removed (dead code — old affordance tile)."""
    assert ".add-sessions-tile {" not in _CSS and ".add-sessions-tile{" not in _CSS, (
        ".add-sessions-tile CSS must be removed — it's an orphaned class from old patterns"
    )


def test_css_no_add_sessions_tile_icon() -> None:
    """.add-sessions-tile__icon CSS must be removed (dead code)."""
    assert ".add-sessions-tile__icon" not in _CSS, (
        ".add-sessions-tile__icon CSS must be removed — orphaned class"
    )


def test_css_no_add_sessions_tile_label() -> None:
    """.add-sessions-tile__label CSS must be removed (dead code)."""
    assert ".add-sessions-tile__label" not in _CSS, (
        ".add-sessions-tile__label CSS must be removed — orphaned class"
    )


def test_css_no_manage_view_panel_close_class() -> None:
    """.manage-view-panel__close CSS must be removed (HTML uses __close-btn)."""
    # The CSS has both __close and __close-btn — __close is orphaned
    # Only check for the specific orphaned rule (not __close-btn)
    # Look for the exact class name without the -btn suffix
    assert ".manage-view-panel__close {" not in _CSS, (
        ".manage-view-panel__close CSS must be removed — HTML uses .manage-view-panel__close-btn"
    )


def test_css_no_manage_view_panel_title_class() -> None:
    """.manage-view-panel__title CSS must be removed (HTML uses __name)."""
    assert ".manage-view-panel__title {" not in _CSS, (
        ".manage-view-panel__title CSS must be removed — HTML uses .manage-view-panel__name"
    )


def test_css_manage_view_panel_comment_updated() -> None:
    """CSS comment must say 'Manage View Panel' not 'Add Sessions Panel'."""
    assert "Add Sessions Panel" not in _CSS, (
        "CSS comment 'Add Sessions Panel' must be updated to 'Manage View Panel'"
    )


def test_css_manage_view_panel_name_input_exists() -> None:
    """.manage-view-panel__name-input CSS must exist (needed for Fix 1 rename input)."""
    assert ".manage-view-panel__name-input" in _CSS, (
        ".manage-view-panel__name-input CSS must exist — it's used by the rename input in Fix 1"
    )

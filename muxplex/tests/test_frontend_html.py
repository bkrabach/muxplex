"""Tests for frontend/index.html — verifies presence of all required DOM elements."""

import pathlib

from bs4 import BeautifulSoup, Tag

HTML_PATH = pathlib.Path(__file__).parent.parent / "frontend" / "index.html"
LOGIN_PATH = pathlib.Path(__file__).parent.parent / "frontend" / "login.html"

# Parse once per module — tests are read-only so sharing is safe.
_SOUP: BeautifulSoup = BeautifulSoup(HTML_PATH.read_text(), "html.parser")


def test_html_pwa_meta() -> None:
    """apple-mobile-web-app-capable, rel=manifest, theme-color, apple-mobile-web-app-status-bar-style."""
    soup = _SOUP
    # rel=manifest
    assert soup.find("link", rel="manifest"), "Missing <link rel='manifest'>"
    # theme-color
    assert soup.find("meta", attrs={"name": "theme-color"}), (
        "Missing <meta name='theme-color'>"
    )
    # apple-mobile-web-app-capable
    assert soup.find("meta", attrs={"name": "apple-mobile-web-app-capable"}), (
        "Missing <meta name='apple-mobile-web-app-capable'>"
    )
    # apple-mobile-web-app-status-bar-style
    assert soup.find("meta", attrs={"name": "apple-mobile-web-app-status-bar-style"}), (
        "Missing <meta name='apple-mobile-web-app-status-bar-style'>"
    )


def test_html_viewport_suppresses_pinch_zoom() -> None:
    """viewport must include maximum-scale=1.0 and user-scalable=no."""
    soup = _SOUP
    viewport = soup.find("meta", attrs={"name": "viewport"})
    assert viewport, "Missing <meta name='viewport'>"
    content = str(viewport.get("content", ""))  # type: ignore[union-attr]
    assert "maximum-scale=1.0" in content, (
        f"viewport missing maximum-scale=1.0: {content!r}"
    )
    assert "user-scalable=no" in content, (
        f"viewport missing user-scalable=no: {content!r}"
    )


def test_html_required_views() -> None:
    """id=view-overview, view-expanded, session-grid, terminal-container, empty-state."""
    soup = _SOUP
    for id_ in (
        "view-overview",
        "view-expanded",
        "session-grid",
        "terminal-container",
        "empty-state",
    ):
        assert soup.find(id=id_), f"Missing element with id='{id_}'"


def test_html_expanded_view_elements() -> None:
    """id=back-btn, expanded-session-name, reconnect-overlay."""
    soup = _SOUP
    for id_ in (
        "back-btn",
        "expanded-session-name",
        "reconnect-overlay",
    ):
        assert soup.find(id=id_), f"Missing element with id='{id_}'"


def test_html_bottom_sheet() -> None:
    """id=bottom-sheet, sheet-list, sheet-backdrop, session-pill, session-pill-label, session-pill-bell."""
    soup = _SOUP
    for id_ in (
        "bottom-sheet",
        "sheet-list",
        "sheet-backdrop",
        "session-pill",
        "session-pill-label",
        "session-pill-bell",
    ):
        assert soup.find(id=id_), f"Missing element with id='{id_}'"


def test_html_toast() -> None:
    """id=toast, aria-live=polite."""
    soup = _SOUP
    toast = soup.find(id="toast")
    assert toast, "Missing element with id='toast'"
    assert toast.get("aria-live") == "polite", (  # type: ignore[union-attr]
        f"toast missing aria-live=polite, got: {toast.get('aria-live')!r}"  # type: ignore[union-attr]
    )


def test_html_scripts() -> None:
    """src=/app.js, src=/terminal.js, xterm."""
    soup = _SOUP
    scripts = soup.find_all("script")
    srcs = [str(s.get("src", "")) for s in scripts]
    assert any("/app.js" in s for s in srcs), (
        f"Missing script src=/app.js; found: {srcs}"
    )
    assert any("/terminal.js" in s for s in srcs), (
        f"Missing script src=/terminal.js; found: {srcs}"
    )
    assert any("xterm" in s for s in srcs), f"Missing xterm script; found: {srcs}"


def test_html_xterm_css() -> None:
    """xterm.css CDN link present."""
    soup = _SOUP
    links = soup.find_all("link", rel="stylesheet")
    hrefs = [str(lnk.get("href", "")) for lnk in links]
    assert any("xterm.css" in h for h in hrefs), (
        f"Missing xterm.css link; found: {hrefs}"
    )


def test_html_style_css() -> None:
    """href=/style.css present."""
    soup = _SOUP
    links = soup.find_all("link", rel="stylesheet")
    hrefs = [str(lnk.get("href", "")) for lnk in links]
    assert any("/style.css" in h for h in hrefs), (
        f"Missing /style.css link; found: {hrefs}"
    )


def test_html_sidebar_toggle_button() -> None:
    """#sidebar-toggle-btn must exist in expanded-header with correct aria-label and hamburger icon."""
    soup = _SOUP
    btn = soup.find(id="sidebar-toggle-btn")
    assert btn is not None, "Missing #sidebar-toggle-btn"
    assert btn.get("aria-label") == "Toggle session list", (
        f"#sidebar-toggle-btn aria-label must be 'Toggle session list', got: {btn.get('aria-label')!r}"
    )
    # Must be inside expanded-header
    header = soup.find("header", class_="expanded-header")
    assert header is not None, "Missing header.expanded-header"
    assert header.find(id="sidebar-toggle-btn") is not None, (
        "#sidebar-toggle-btn must be inside header.expanded-header"
    )
    # Must be after #back-btn and before #expanded-session-name
    header_children_ids = [
        el.get("id") for el in header.children if isinstance(el, Tag)
    ]
    header_children_ids = [i for i in header_children_ids if i]
    assert "back-btn" in header_children_ids, "#back-btn must be in expanded-header"
    assert "sidebar-toggle-btn" in header_children_ids, (
        "#sidebar-toggle-btn must be in expanded-header"
    )
    assert "expanded-session-name" in header_children_ids, (
        "#expanded-session-name must be in expanded-header"
    )
    back_idx = header_children_ids.index("back-btn")
    toggle_idx = header_children_ids.index("sidebar-toggle-btn")
    name_idx = header_children_ids.index("expanded-session-name")
    assert back_idx < toggle_idx < name_idx, (
        f"Order must be back-btn < sidebar-toggle-btn < expanded-session-name, got indices {back_idx}, {toggle_idx}, {name_idx}"
    )


def test_html_view_body_wrapper() -> None:
    """.view-body div must exist inside #view-expanded wrapping #session-sidebar and #terminal-container."""
    soup = _SOUP
    view_expanded = soup.find(id="view-expanded")
    assert view_expanded is not None, "Missing #view-expanded"
    view_body = view_expanded.find("div", class_="view-body")
    assert view_body is not None, "Missing div.view-body inside #view-expanded"
    # #terminal-container must be inside .view-body
    assert view_body.find(id="terminal-container") is not None, (
        "#terminal-container must be inside div.view-body"
    )
    # #session-sidebar must be inside .view-body
    assert view_body.find(id="session-sidebar") is not None, (
        "#session-sidebar must be inside div.view-body"
    )


def test_html_reconnect_overlay_outside_view_body() -> None:
    """#reconnect-overlay must be a direct child of #view-expanded, NOT inside .view-body."""
    soup = _SOUP
    view_expanded = soup.find(id="view-expanded")
    assert view_expanded is not None, "Missing #view-expanded"
    view_body = view_expanded.find("div", class_="view-body")
    assert view_body is not None, "Missing div.view-body"
    # reconnect-overlay must NOT be inside view-body
    assert view_body.find(id="reconnect-overlay") is None, (
        "#reconnect-overlay must NOT be inside div.view-body"
    )
    # reconnect-overlay must be inside view-expanded (as sibling of view-body)
    assert view_expanded.find(id="reconnect-overlay") is not None, (
        "#reconnect-overlay must be inside #view-expanded"
    )


def test_html_session_sidebar_structure() -> None:
    """#session-sidebar must contain .sidebar-header (with .sidebar-title and #sidebar-collapse-btn) and #sidebar-list."""
    soup = _SOUP
    sidebar = soup.find(id="session-sidebar")
    assert sidebar is not None, "Missing #session-sidebar"
    # .sidebar-header
    sidebar_header = sidebar.find(class_="sidebar-header")
    assert sidebar_header is not None, "Missing .sidebar-header inside #session-sidebar"
    # .sidebar-title with text 'Sessions'
    sidebar_title = sidebar_header.find(class_="sidebar-title")
    assert sidebar_title is not None, "Missing .sidebar-title inside .sidebar-header"
    assert "Sessions" in sidebar_title.get_text(), (
        f".sidebar-title text must contain 'Sessions', got: {sidebar_title.get_text()!r}"
    )
    # #sidebar-collapse-btn
    collapse_btn = sidebar_header.find(id="sidebar-collapse-btn")
    assert collapse_btn is not None, (
        "Missing #sidebar-collapse-btn inside .sidebar-header"
    )
    # #sidebar-list
    sidebar_list = sidebar.find(id="sidebar-list")
    assert sidebar_list is not None, "Missing #sidebar-list inside #session-sidebar"


def test_html_element_classes() -> None:
    """Critical and important elements must carry their CSS styling classes."""
    soup = _SOUP
    cases = [
        # (element_id, required_class, reason)
        ("terminal-container", "terminal-container", "xterm.js needs flex:1 to render"),
        (
            "reconnect-overlay",
            "reconnect-overlay",
            "needs position:absolute to overlay terminal",
        ),
        ("session-pill", "session-pill", "needs position:fixed to float"),
        ("toast", "toast", "needs position:fixed and animation"),
        ("back-btn", "back-btn", "needs border and hover styles"),
        (
            "expanded-session-name",
            "expanded-session-name",
            "needs text-overflow:ellipsis",
        ),
        ("session-pill-label", "session-pill__label", "needs max-width truncation"),
        ("session-pill-bell", "session-pill__bell", "needs amber var(--bell) color"),
        (
            "session-sidebar",
            "session-sidebar",
            "flex-column layout and collapse transition",
        ),
        ("sidebar-toggle-btn", "sidebar-toggle-btn", "36x36 bordered button styles"),
        ("sidebar-collapse-btn", "sidebar-collapse-btn", "chevron button hover styles"),
        ("sidebar-list", "sidebar-list", "flex:1 overflow-y:auto scroll container"),
    ]
    for el_id, expected_class, reason in cases:
        el = soup.find(id=el_id)
        assert el is not None, f"#{el_id} not found in HTML"
        classes = el.get("class") or []
        assert expected_class in classes, (
            f"#{el_id} is missing class '{expected_class}' — {reason}. Has: {classes}"
        )


# ── Login page tests ─────────────────────────────────────────────────────────


def _get_login_soup() -> BeautifulSoup:
    """Load and parse login.html — raises FileNotFoundError if file is missing."""
    return BeautifulSoup(LOGIN_PATH.read_text(), "html.parser")


def test_login_html_exists() -> None:
    """login.html must exist in the frontend directory."""
    assert LOGIN_PATH.exists(), f"Missing login.html at {LOGIN_PATH}"


def test_login_html_has_form() -> None:
    """login.html must have a <form> that POSTs to /login."""
    soup = _get_login_soup()
    form = soup.find("form")
    assert form is not None, "Missing <form> element in login.html"
    method = str(form.get("method", "")).lower()  # type: ignore[union-attr]
    assert method == "post", f"Form method must be 'post', got: {method!r}"
    action = str(form.get("action", ""))  # type: ignore[union-attr]
    assert action == "/login", f"Form action must be '/login', got: {action!r}"


def test_login_html_has_password_autocomplete() -> None:
    """Password input must have autocomplete='current-password'."""
    soup = _get_login_soup()
    pw = soup.find("input", attrs={"type": "password"})
    assert pw is not None, "Missing <input type='password'> in login.html"
    ac = str(pw.get("autocomplete", ""))  # type: ignore[union-attr]
    assert ac == "current-password", (
        f"Password input must have autocomplete='current-password', got: {ac!r}"
    )


def test_login_html_has_wordmark() -> None:
    """login.html must include an <img> whose src contains 'wordmark'."""
    soup = _get_login_soup()
    imgs = soup.find_all("img")
    srcs = [str(img.get("src", "")) for img in imgs]
    assert any("wordmark" in s for s in srcs), (
        f"No <img> with src containing 'wordmark' found; srcs: {srcs}"
    )


def test_login_html_references_muxplex_auth() -> None:
    """login.html must reference MUXPLEX_AUTH (for JS-based auth mode detection)."""
    text = LOGIN_PATH.read_text()
    assert "MUXPLEX_AUTH" in text, (
        "login.html must reference window.MUXPLEX_AUTH for auth mode detection"
    )


def test_login_html_has_error_display() -> None:
    """login.html must include an error display element (text 'error' must appear)."""
    text = LOGIN_PATH.read_text().lower()
    assert "error" in text, (
        "login.html must include an error display element (text 'error' not found)"
    )


# ============================================================
# Settings modal (task-6)
# ============================================================


def test_html_header_actions_div() -> None:
    """overview header must have .header-actions div containing #new-session-btn, #settings-btn, and #connection-status."""
    soup = _SOUP
    view_overview = soup.find(id="view-overview")
    assert view_overview is not None, "Missing #view-overview"
    header = view_overview.find("header", class_="app-header")
    assert header is not None, "Missing header.app-header inside #view-overview"
    header_actions = header.find(class_="header-actions")
    assert header_actions is not None, "Missing .header-actions inside app-header"


def test_html_new_session_btn() -> None:
    """#new-session-btn must exist in .header-actions with class header-btn; #new-session-fab must also exist."""
    soup = _SOUP
    btn = soup.find(id="new-session-btn")
    assert btn is not None, "Missing #new-session-btn"
    classes = btn.get("class") or []
    assert "header-btn" in classes, (
        f"#new-session-btn must have class 'header-btn', has: {classes}"
    )
    # Must be inside header-actions
    header_actions = soup.find(class_="header-actions")
    assert header_actions is not None, "Missing .header-actions"
    assert header_actions.find(id="new-session-btn") is not None, (
        "#new-session-btn must be inside .header-actions"
    )
    # Mobile FAB button must also exist
    assert soup.find(id="new-session-fab") is not None, "Missing #new-session-fab"


def test_html_settings_btn() -> None:
    """#settings-btn must exist in .header-actions with class header-btn."""
    soup = _SOUP
    btn = soup.find(id="settings-btn")
    assert btn is not None, "Missing #settings-btn"
    classes = btn.get("class") or []
    assert "header-btn" in classes, (
        f"#settings-btn must have class 'header-btn', has: {classes}"
    )
    # Must be inside header-actions
    header_actions = soup.find(class_="header-actions")
    assert header_actions is not None, "Missing .header-actions"
    assert header_actions.find(id="settings-btn") is not None, (
        "#settings-btn must be inside .header-actions"
    )


def test_html_settings_btn_expanded() -> None:
    """#settings-btn-expanded must exist in expanded-header after #expanded-session-name."""
    soup = _SOUP
    btn = soup.find(id="settings-btn-expanded")
    assert btn is not None, "Missing #settings-btn-expanded"
    classes = btn.get("class") or []
    assert "header-btn" in classes, (
        f"#settings-btn-expanded must have class 'header-btn', has: {classes}"
    )
    # Must be inside expanded-header
    header = soup.find("header", class_="expanded-header")
    assert header is not None, "Missing header.expanded-header"
    assert header.find(id="settings-btn-expanded") is not None, (
        "#settings-btn-expanded must be inside header.expanded-header"
    )
    # Must appear after #expanded-session-name
    header_children_ids = [
        el.get("id") for el in header.children if isinstance(el, Tag)
    ]
    header_children_ids = [i for i in header_children_ids if i]
    assert "expanded-session-name" in header_children_ids, (
        "#expanded-session-name must be in expanded-header"
    )
    assert "settings-btn-expanded" in header_children_ids, (
        "#settings-btn-expanded must be in expanded-header"
    )
    name_idx = header_children_ids.index("expanded-session-name")
    settings_idx = header_children_ids.index("settings-btn-expanded")
    assert name_idx < settings_idx, (
        f"#settings-btn-expanded must come after #expanded-session-name, got indices {name_idx}, {settings_idx}"
    )


def test_html_settings_backdrop() -> None:
    """#settings-backdrop must exist with class settings-backdrop and hidden."""
    soup = _SOUP
    el = soup.find(id="settings-backdrop")
    assert el is not None, "Missing #settings-backdrop"
    classes = el.get("class") or []
    assert "settings-backdrop" in classes, (
        f"#settings-backdrop must have class 'settings-backdrop', has: {classes}"
    )
    assert "hidden" in classes, (
        f"#settings-backdrop must have class 'hidden', has: {classes}"
    )


def test_html_settings_dialog() -> None:
    """#settings-dialog must be a <dialog> with class settings-dialog; #settings-backdrop and #settings-btn must also exist."""
    soup = _SOUP
    el = soup.find(id="settings-dialog")
    assert el is not None, "Missing #settings-dialog"
    assert el.name == "dialog", (
        f"#settings-dialog must be a <dialog> element, got: {el.name}"
    )
    classes = el.get("class") or []
    assert "settings-dialog" in classes, (
        f"#settings-dialog must have class 'settings-dialog', has: {classes}"
    )
    assert soup.find(id="settings-backdrop") is not None, "Missing #settings-backdrop"
    assert soup.find(id="settings-btn") is not None, "Missing #settings-btn"


def test_html_settings_tabs() -> None:
    """settings-dialog must contain 4 tab buttons with correct data-tab values."""
    soup = _SOUP
    dialog = soup.find(id="settings-dialog")
    assert dialog is not None, "Missing #settings-dialog"
    tabs_container = dialog.find("nav", class_="settings-tabs")
    assert tabs_container is not None, (
        "Missing nav.settings-tabs inside #settings-dialog"
    )
    expected_tabs = ["display", "sessions", "new-session", "devices"]
    for tab_value in expected_tabs:
        tab = tabs_container.find("button", attrs={"data-tab": tab_value})
        assert tab is not None, (
            f"Missing tab button with data-tab='{tab_value}' in settings-tabs"
        )
    # Display tab must be active by default
    display_tab = tabs_container.find("button", attrs={"data-tab": "display"})
    assert display_tab is not None
    display_classes = display_tab.get("class") or []
    assert "settings-tab--active" in display_classes, (
        f"Display tab must have class 'settings-tab--active', has: {display_classes}"
    )
    # All tabs must have settings-tab class
    for tab_value in expected_tabs:
        tab = tabs_container.find("button", attrs={"data-tab": tab_value})
        assert tab is not None, f"Missing tab button with data-tab='{tab_value}'"
        tab_classes = tab.get("class") or []
        assert "settings-tab" in tab_classes, (
            f"Tab data-tab='{tab_value}' must have class 'settings-tab', has: {tab_classes}"
        )


def test_html_settings_display_panel_controls() -> None:
    """Display panel must have font-size, hover-delay, and grid-columns selects."""
    soup = _SOUP
    dialog = soup.find(id="settings-dialog")
    assert dialog is not None, "Missing #settings-dialog"
    for ctrl_id in ("setting-font-size", "setting-hover-delay", "setting-grid-columns"):
        el = dialog.find(id=ctrl_id)
        assert el is not None, f"Missing #{ctrl_id} inside #settings-dialog"
        assert el.name == "select", (
            f"#{ctrl_id} must be a <select> element, got: {el.name}"
        )


def test_html_settings_font_size_options() -> None:
    """setting-font-size select must have options 11, 12, 13, 14 (selected), 16."""
    soup = _SOUP
    select = soup.find(id="setting-font-size")
    assert select is not None, "Missing #setting-font-size"
    options = select.find_all("option")
    values = [o.get("value") for o in options]
    for v in ("11", "12", "13", "14", "16"):
        assert v in values, f"#setting-font-size missing option value='{v}'"
    # 14 must be selected
    selected_opt = select.find("option", attrs={"selected": True})
    assert selected_opt is not None, "setting-font-size must have a selected option"
    assert selected_opt.get("value") == "14", (
        f"setting-font-size default selection must be 14, got: {selected_opt.get('value')}"
    )


def test_html_settings_hover_delay_options() -> None:
    """setting-hover-delay select must have Off (0), 1000, 1500 (selected), 2000, 3000."""
    soup = _SOUP
    select = soup.find(id="setting-hover-delay")
    assert select is not None, "Missing #setting-hover-delay"
    options = select.find_all("option")
    values = [o.get("value") for o in options]
    for v in ("0", "1000", "1500", "2000", "3000"):
        assert v in values, f"#setting-hover-delay missing option value='{v}'"
    # 1500 must be selected
    selected_opt = select.find("option", attrs={"selected": True})
    assert selected_opt is not None, "setting-hover-delay must have a selected option"
    assert selected_opt.get("value") == "1500", (
        f"setting-hover-delay default selection must be 1500, got: {selected_opt.get('value')}"
    )


def test_html_settings_grid_columns_options() -> None:
    """setting-grid-columns select must have auto (selected), 2, 3, 4."""
    soup = _SOUP
    select = soup.find(id="setting-grid-columns")
    assert select is not None, "Missing #setting-grid-columns"
    options = select.find_all("option")
    values = [o.get("value") for o in options]
    for v in ("auto", "2", "3", "4"):
        assert v in values, f"#setting-grid-columns missing option value='{v}'"
    # auto must be selected
    selected_opt = select.find("option", attrs={"selected": True})
    assert selected_opt is not None, "setting-grid-columns must have a selected option"
    assert selected_opt.get("value") == "auto", (
        f"setting-grid-columns default selection must be auto, got: {selected_opt.get('value')}"
    )


def test_html_settings_panels_use_data_tab() -> None:
    """settings-panel elements must use data-tab (not data-panel) to match switchSettingsTab().

    switchSettingsTab() in app.js reads panel.dataset.tab which corresponds to the
    data-tab HTML attribute. If panels use data-panel instead, all panels get hidden
    on the first tab click — the entire settings dialog becomes non-functional.
    """
    soup = _SOUP
    dialog = soup.find(id="settings-dialog")
    assert dialog is not None, "Missing #settings-dialog"
    panels = dialog.find_all(class_="settings-panel")
    assert len(panels) == 4, (
        f"Expected 4 .settings-panel elements, found: {len(panels)}"
    )
    for panel in panels:
        assert panel.get("data-tab") is not None, (
            f"settings-panel must use data-tab attribute, found: {dict(panel.attrs)}"
        )
        assert panel.get("data-panel") is None, (
            f"settings-panel must NOT use data-panel attribute (use data-tab instead), found: {dict(panel.attrs)}"
        )


def test_html_settings_tab_panel_data_tab_alignment() -> None:
    """Every tab button data-tab value must have a matching settings-panel data-tab value.

    Cross-check test: verifies the HTML attribute names are consistent between tab buttons
    (which use data-tab) and content panels (which must also use data-tab). A mismatch
    causes switchSettingsTab() to read undefined for every panel, hiding all panels.
    """
    soup = _SOUP
    dialog = soup.find(id="settings-dialog")
    assert dialog is not None, "Missing #settings-dialog"

    # Collect tab button data-tab values
    tabs_container = dialog.find("nav", class_="settings-tabs")
    assert tabs_container is not None, "Missing nav.settings-tabs"
    tab_buttons = tabs_container.find_all("button", attrs={"data-tab": True})
    tab_values = {str(btn.get("data-tab")) for btn in tab_buttons}
    assert len(tab_values) > 0, "No tab buttons with data-tab found"

    # Collect panel data-tab values
    panels = dialog.find_all(class_="settings-panel")
    panel_values = {
        str(p.get("data-tab")) for p in panels if p.get("data-tab") is not None
    }

    # Every tab button must have a matching panel
    missing_panels = tab_values - panel_values
    assert not missing_panels, (
        f"Tab buttons {missing_panels} have no matching settings-panel[data-tab=...]. "
        f"Panel data-tab values found: {panel_values}"
    )


# ============================================================
# Sessions tab (task-1-sessions-tab)
# ============================================================


def test_html_sessions_panel_has_default_session_select() -> None:
    """Sessions panel must contain a #setting-default-session select."""
    soup = _SOUP
    dialog = soup.find(id="settings-dialog")
    assert dialog is not None, "Missing #settings-dialog"
    sessions_panel = dialog.find(
        class_="settings-panel", attrs={"data-tab": "sessions"}
    )
    assert sessions_panel is not None, "Missing sessions settings-panel"
    el = sessions_panel.find(id="setting-default-session")
    assert el is not None, "Missing #setting-default-session inside sessions panel"
    assert el.name == "select", (
        f"#setting-default-session must be a <select>, got: {el.name}"
    )


def test_html_sessions_panel_has_sort_order_select() -> None:
    """Sessions panel must contain a #setting-sort-order select with manual/alphabetical/recent options."""
    soup = _SOUP
    dialog = soup.find(id="settings-dialog")
    assert dialog is not None, "Missing #settings-dialog"
    sessions_panel = dialog.find(
        class_="settings-panel", attrs={"data-tab": "sessions"}
    )
    assert sessions_panel is not None, "Missing sessions settings-panel"
    el = sessions_panel.find(id="setting-sort-order")
    assert el is not None, "Missing #setting-sort-order inside sessions panel"
    assert el.name == "select", (
        f"#setting-sort-order must be a <select>, got: {el.name}"
    )
    options = el.find_all("option")
    values = [o.get("value") for o in options]
    for v in ("manual", "alphabetical", "recent"):
        assert v in values, f"#setting-sort-order missing option value='{v}'"


def test_html_sessions_panel_has_hidden_sessions_container() -> None:
    """Sessions panel must contain a #setting-hidden-sessions container for checkboxes."""
    soup = _SOUP
    dialog = soup.find(id="settings-dialog")
    assert dialog is not None, "Missing #settings-dialog"
    sessions_panel = dialog.find(
        class_="settings-panel", attrs={"data-tab": "sessions"}
    )
    assert sessions_panel is not None, "Missing sessions settings-panel"
    el = sessions_panel.find(id="setting-hidden-sessions")
    assert el is not None, "Missing #setting-hidden-sessions inside sessions panel"


def test_html_sessions_panel_has_window_size_largest_checkbox() -> None:
    """Sessions panel must contain a #setting-window-size-largest checkbox."""
    soup = _SOUP
    dialog = soup.find(id="settings-dialog")
    assert dialog is not None, "Missing #settings-dialog"
    sessions_panel = dialog.find(
        class_="settings-panel", attrs={"data-tab": "sessions"}
    )
    assert sessions_panel is not None, "Missing sessions settings-panel"
    el = sessions_panel.find(id="setting-window-size-largest")
    assert el is not None, "Missing #setting-window-size-largest inside sessions panel"
    assert el.name == "input", (
        f"#setting-window-size-largest must be an <input>, got: {el.name}"
    )
    assert el.get("type") == "checkbox", (
        f"#setting-window-size-largest must be type='checkbox', got: {el.get('type')}"
    )


def test_html_sessions_panel_has_auto_open_checkbox_default_checked() -> None:
    """Sessions panel must contain a #setting-auto-open checkbox with default checked."""
    soup = _SOUP
    dialog = soup.find(id="settings-dialog")
    assert dialog is not None, "Missing #settings-dialog"
    sessions_panel = dialog.find(
        class_="settings-panel", attrs={"data-tab": "sessions"}
    )
    assert sessions_panel is not None, "Missing sessions settings-panel"
    el = sessions_panel.find(id="setting-auto-open")
    assert el is not None, "Missing #setting-auto-open inside sessions panel"
    assert el.name == "input", f"#setting-auto-open must be an <input>, got: {el.name}"
    assert el.get("type") == "checkbox", (
        f"#setting-auto-open must be type='checkbox', got: {el.get('type')}"
    )
    assert el.get("checked") is not None, (
        "#setting-auto-open must be checked by default"
    )


# ============================================================
# Notifications tab (task-2-notifications-tab)
# ============================================================


def test_html_notifications_panel_has_bell_sound_checkbox() -> None:
    """Sessions panel must contain a #setting-bell-sound checkbox."""
    soup = _SOUP
    dialog = soup.find(id="settings-dialog")
    assert dialog is not None, "Missing #settings-dialog"
    notif_panel = dialog.find(
        class_="settings-panel", attrs={"data-tab": "sessions"}
    )
    assert notif_panel is not None, "Missing sessions settings-panel"
    el = notif_panel.find(id="setting-bell-sound")
    assert el is not None, "Missing #setting-bell-sound inside notifications panel"
    assert el.name == "input", f"#setting-bell-sound must be an <input>, got: {el.name}"
    assert el.get("type") == "checkbox", (
        f"#setting-bell-sound must be type='checkbox', got: {el.get('type')}"
    )
    classes = el.get("class") or []
    assert "settings-checkbox" in classes, (
        f"#setting-bell-sound must have class 'settings-checkbox', has: {classes}"
    )


def test_html_notifications_panel_has_notification_status_text() -> None:
    """Sessions panel must contain #notification-status-text with class settings-status-text."""
    soup = _SOUP
    dialog = soup.find(id="settings-dialog")
    assert dialog is not None, "Missing #settings-dialog"
    notif_panel = dialog.find(
        class_="settings-panel", attrs={"data-tab": "sessions"}
    )
    assert notif_panel is not None, "Missing sessions settings-panel"
    el = notif_panel.find(id="notification-status-text")
    assert el is not None, (
        "Missing #notification-status-text inside notifications panel"
    )
    classes = el.get("class") or []
    assert "settings-status-text" in classes, (
        f"#notification-status-text must have class 'settings-status-text', has: {classes}"
    )


def test_html_notifications_panel_has_request_btn() -> None:
    """Sessions panel must contain #notification-request-btn with class settings-action-btn."""
    soup = _SOUP
    dialog = soup.find(id="settings-dialog")
    assert dialog is not None, "Missing #settings-dialog"
    notif_panel = dialog.find(
        class_="settings-panel", attrs={"data-tab": "sessions"}
    )
    assert notif_panel is not None, "Missing sessions settings-panel"
    el = notif_panel.find(id="notification-request-btn")
    assert el is not None, (
        "Missing #notification-request-btn inside notifications panel"
    )
    classes = el.get("class") or []
    assert "settings-action-btn" in classes, (
        f"#notification-request-btn must have class 'settings-action-btn', has: {classes}"
    )


# ============================================================
# New Session tab (task-3-new-session-tab)
# ============================================================


def test_html_new_session_panel_exists() -> None:
    """New Session settings panel must exist with data-tab='new-session'."""
    soup = _SOUP
    dialog = soup.find(id="settings-dialog")
    assert dialog is not None, "Missing #settings-dialog"
    new_session_panel = dialog.find(
        class_="settings-panel", attrs={"data-tab": "new-session"}
    )
    assert new_session_panel is not None, "Missing new-session settings-panel"


def test_html_new_session_panel_has_settings_field_column() -> None:
    """New Session panel must contain a .settings-field--column div."""
    soup = _SOUP
    dialog = soup.find(id="settings-dialog")
    assert dialog is not None, "Missing #settings-dialog"
    new_session_panel = dialog.find(
        class_="settings-panel", attrs={"data-tab": "new-session"}
    )
    assert new_session_panel is not None, "Missing new-session settings-panel"
    field = new_session_panel.find(class_="settings-field--column")
    assert field is not None, "Missing .settings-field--column inside new-session panel"


def test_html_new_session_panel_has_template_label() -> None:
    """New Session panel must have a label for #setting-template with text 'Command template'."""
    soup = _SOUP
    dialog = soup.find(id="settings-dialog")
    assert dialog is not None, "Missing #settings-dialog"
    new_session_panel = dialog.find(
        class_="settings-panel", attrs={"data-tab": "new-session"}
    )
    assert new_session_panel is not None, "Missing new-session settings-panel"
    label = new_session_panel.find("label", attrs={"for": "setting-template"})
    assert label is not None, (
        "Missing <label for='setting-template'> inside new-session panel"
    )
    label_text = label.get_text(strip=True)
    assert "Command template" in label_text, (
        f"Label for #setting-template must contain 'Command template', got: {label_text!r}"
    )


def test_html_new_session_panel_has_template_textarea() -> None:
    """New Session panel must contain #setting-template textarea with class settings-textarea."""
    soup = _SOUP
    dialog = soup.find(id="settings-dialog")
    assert dialog is not None, "Missing #settings-dialog"
    new_session_panel = dialog.find(
        class_="settings-panel", attrs={"data-tab": "new-session"}
    )
    assert new_session_panel is not None, "Missing new-session settings-panel"
    textarea = new_session_panel.find("textarea", id="setting-template")
    assert textarea is not None, (
        "Missing <textarea id='setting-template'> inside new-session panel"
    )
    classes = textarea.get("class") or []
    assert "settings-textarea" in classes, (
        f"#setting-template must have class 'settings-textarea', has: {classes}"
    )


def test_html_new_session_template_textarea_rows() -> None:
    """#setting-template textarea must have rows=3."""
    soup = _SOUP
    textarea = soup.find("textarea", id="setting-template")
    assert textarea is not None, "Missing #setting-template textarea"
    rows = textarea.get("rows")
    assert rows == "3", f"#setting-template must have rows='3', got: {rows!r}"


def test_html_new_session_template_textarea_placeholder() -> None:
    """#setting-template textarea must have placeholder 'tmux new-session -d -s {name}'."""
    soup = _SOUP
    textarea = soup.find("textarea", id="setting-template")
    assert textarea is not None, "Missing #setting-template textarea"
    placeholder = textarea.get("placeholder")
    assert placeholder == "tmux new-session -d -s {name}", (
        f"#setting-template placeholder must be 'tmux new-session -d -s {{name}}', got: {placeholder!r}"
    )


def test_html_new_session_panel_has_helper_text() -> None:
    """New Session panel must contain a .settings-helper span with '{name} is replaced...' text."""
    soup = _SOUP
    dialog = soup.find(id="settings-dialog")
    assert dialog is not None, "Missing #settings-dialog"
    new_session_panel = dialog.find(
        class_="settings-panel", attrs={"data-tab": "new-session"}
    )
    assert new_session_panel is not None, "Missing new-session settings-panel"
    helper = new_session_panel.find(class_="settings-helper")
    assert helper is not None, "Missing .settings-helper inside new-session panel"
    helper_text = helper.get_text(strip=True)
    assert "{name}" in helper_text, (
        f".settings-helper must contain '{{name}}', got: {helper_text!r}"
    )
    assert "replaced" in helper_text.lower() or "session name" in helper_text.lower(), (
        f".settings-helper must describe what {{name}} is replaced with, got: {helper_text!r}"
    )


def test_html_new_session_panel_has_reset_button() -> None:
    """New Session panel must contain #setting-template-reset button with class settings-action-btn."""
    soup = _SOUP
    dialog = soup.find(id="settings-dialog")
    assert dialog is not None, "Missing #settings-dialog"
    new_session_panel = dialog.find(
        class_="settings-panel", attrs={"data-tab": "new-session"}
    )
    assert new_session_panel is not None, "Missing new-session settings-panel"
    reset_btn = new_session_panel.find(id="setting-template-reset")
    assert reset_btn is not None, (
        "Missing #setting-template-reset inside new-session panel"
    )
    classes = reset_btn.get("class") or []
    assert "settings-action-btn" in classes, (
        f"#setting-template-reset must have class 'settings-action-btn', has: {classes}"
    )


# ============================================================
# Sidebar sticky footer (task-5-sidebar-new-footer)
# ============================================================


def test_html_sidebar_footer_exists() -> None:
    """#session-sidebar must contain a div.sidebar-footer after #sidebar-list."""
    soup = _SOUP
    sidebar = soup.find(id="session-sidebar")
    assert sidebar is not None, "Missing #session-sidebar"
    footer = sidebar.find("div", class_="sidebar-footer")
    assert footer is not None, "Missing div.sidebar-footer inside #session-sidebar"


def test_html_sidebar_footer_after_sidebar_list() -> None:
    """div.sidebar-footer must appear after #sidebar-list inside #session-sidebar."""
    soup = _SOUP
    sidebar = soup.find(id="session-sidebar")
    assert sidebar is not None, "Missing #session-sidebar"
    children = [el for el in sidebar.children if isinstance(el, Tag)]
    child_ids_and_classes = []
    for el in children:
        cid = el.get("id")
        cls = el.get("class") or []
        child_ids_and_classes.append((cid, cls))

    # Find sidebar-list and sidebar-footer positions
    list_idx = None
    footer_idx = None
    for i, (cid, cls) in enumerate(child_ids_and_classes):
        if cid == "sidebar-list":
            list_idx = i
        if "sidebar-footer" in cls:
            footer_idx = i

    assert list_idx is not None, "#sidebar-list must be in #session-sidebar children"
    assert footer_idx is not None, (
        "div.sidebar-footer must be in #session-sidebar children"
    )
    assert list_idx < footer_idx, (
        f"div.sidebar-footer must appear after #sidebar-list, "
        f"got list_idx={list_idx}, footer_idx={footer_idx}"
    )


def test_html_sidebar_new_session_btn_exists() -> None:
    """#sidebar-new-session-btn must exist inside div.sidebar-footer."""
    soup = _SOUP
    sidebar = soup.find(id="session-sidebar")
    assert sidebar is not None, "Missing #session-sidebar"
    footer = sidebar.find("div", class_="sidebar-footer")
    assert footer is not None, "Missing div.sidebar-footer inside #session-sidebar"
    btn = footer.find(id="sidebar-new-session-btn")
    assert btn is not None, "Missing #sidebar-new-session-btn inside div.sidebar-footer"


def test_html_sidebar_new_session_btn_class() -> None:
    """#sidebar-new-session-btn must have class sidebar-new-btn."""
    soup = _SOUP
    btn = soup.find(id="sidebar-new-session-btn")
    assert btn is not None, "Missing #sidebar-new-session-btn"
    classes = btn.get("class") or []
    assert "sidebar-new-btn" in classes, (
        f"#sidebar-new-session-btn must have class 'sidebar-new-btn', has: {classes}"
    )


def test_html_sidebar_new_session_btn_text() -> None:
    """#sidebar-new-session-btn must have text '+ New'."""
    soup = _SOUP
    btn = soup.find(id="sidebar-new-session-btn")
    assert btn is not None, "Missing #sidebar-new-session-btn"
    text = btn.get_text(strip=True)
    assert text == "+ New", (
        f"#sidebar-new-session-btn text must be '+ New', got: {text!r}"
    )


def test_html_sidebar_structure_complete() -> None:
    """#session-sidebar must have sidebar-header, sidebar-list, and sidebar-footer in order."""
    soup = _SOUP
    sidebar = soup.find(id="session-sidebar")
    assert sidebar is not None, "Missing #session-sidebar"
    header = sidebar.find(class_="sidebar-header")
    list_ = sidebar.find(id="sidebar-list")
    footer = sidebar.find(class_="sidebar-footer")
    assert header is not None, "Missing .sidebar-header in #session-sidebar"
    assert list_ is not None, "Missing #sidebar-list in #session-sidebar"
    assert footer is not None, "Missing .sidebar-footer in #session-sidebar"


# ============================================================
# Mobile FAB (task-6-mobile-fab)
# ============================================================


def test_html_fab_exists() -> None:
    """#new-session-fab button must exist with class new-session-fab, aria-label='New session', text '+'."""
    soup = _SOUP
    fab = soup.find(id="new-session-fab")
    assert fab is not None, "Missing #new-session-fab"
    assert fab.name == "button", f"#new-session-fab must be a <button>, got: {fab.name}"
    classes = fab.get("class") or []
    assert "new-session-fab" in classes, (
        f"#new-session-fab must have class 'new-session-fab', has: {classes}"
    )
    assert fab.get("aria-label") == "New session", (
        f"#new-session-fab must have aria-label='New session', got: {fab.get('aria-label')!r}"
    )
    text = fab.get_text(strip=True)
    assert text == "+", f"#new-session-fab text must be '+', got: {text!r}"


def test_html_fab_before_toast() -> None:
    """#new-session-fab must appear before #toast in the document."""
    soup = _SOUP
    fab = soup.find(id="new-session-fab")
    toast = soup.find(id="toast")
    assert fab is not None, "Missing #new-session-fab"
    assert toast is not None, "Missing #toast"
    # Find positions in the flat list of all elements
    all_elements = list(soup.find_all(True))
    fab_idx = next(
        (i for i, el in enumerate(all_elements) if el.get("id") == "new-session-fab"),
        None,
    )
    toast_idx = next(
        (i for i, el in enumerate(all_elements) if el.get("id") == "toast"),
        None,
    )
    assert fab_idx is not None, "#new-session-fab not found in element list"
    assert toast_idx is not None, "#toast not found in element list"
    assert fab_idx < toast_idx, (
        f"#new-session-fab (idx={fab_idx}) must appear before #toast (idx={toast_idx})"
    )


# ============================================================
# Consolidated settings tests (task-8-frontend-tests)
# ============================================================


def test_html_display_tab_controls() -> None:
    """Display tab panel must contain setting-font-size, setting-hover-delay, setting-grid-columns."""
    soup = _SOUP
    for id_ in ("setting-font-size", "setting-hover-delay", "setting-grid-columns"):
        assert soup.find(id=id_), f"Missing element with id='{id_}'"


def test_html_sidebar_new_session_btn() -> None:
    """#sidebar-new-session-btn must exist in the document."""
    soup = _SOUP
    assert soup.find(id="sidebar-new-session-btn") is not None, (
        "Missing #sidebar-new-session-btn"
    )


def test_html_sessions_tab_controls() -> None:
    """Sessions tab must contain setting-default-session, setting-sort-order, setting-window-size-largest, setting-auto-open."""
    soup = _SOUP
    for id_ in (
        "setting-default-session",
        "setting-sort-order",
        "setting-window-size-largest",
        "setting-auto-open",
    ):
        assert soup.find(id=id_), f"Missing element with id='{id_}'"


def test_html_notifications_tab_controls() -> None:
    """Notifications tab must contain setting-bell-sound and notification-request-btn."""
    soup = _SOUP
    for id_ in ("setting-bell-sound", "notification-request-btn"):
        assert soup.find(id=id_), f"Missing element with id='{id_}'"


def test_html_new_session_tab_controls() -> None:
    """New Session tab must contain setting-template and setting-template-reset."""
    soup = _SOUP
    for id_ in ("setting-template", "setting-template-reset"):
        assert soup.find(id=id_), f"Missing element with id='{id_}'"


def test_html_settings_close_btn_exists() -> None:
    """settings-dialog must contain a #settings-close-btn button to dismiss the modal."""
    soup = _SOUP
    close_btn = soup.find(id="settings-close-btn")
    assert close_btn is not None, "Missing #settings-close-btn inside settings dialog"
    # Must be inside the settings dialog
    dialog = soup.find(id="settings-dialog")
    assert dialog is not None, "Missing #settings-dialog"
    # Verify close button is a descendant of the dialog
    assert dialog.find(id="settings-close-btn") is not None, (
        "#settings-close-btn must be a descendant of #settings-dialog"
    )


# ============================================================
# Remote Instances UI (task-15-remote-instances)
# ============================================================


def test_html_sessions_tab_device_name_input() -> None:
    """Display tab must contain a #setting-device-name text input for the device name."""
    soup = _SOUP
    el = soup.find(id="setting-device-name")
    assert el is not None, "Missing element with id='setting-device-name'"
    # Must be inside the display panel
    display_panel = soup.find("div", attrs={"data-tab": "display"})
    assert display_panel is not None, "Missing display panel (data-tab='display')"
    assert display_panel.find(id="setting-device-name") is not None, (
        "#setting-device-name must be inside the display settings panel"
    )


def test_html_sessions_tab_remote_instances_container() -> None:
    """Multi-Device tab must contain a #setting-remote-instances container for remote instance rows."""
    soup = _SOUP
    el = soup.find(id="setting-remote-instances")
    assert el is not None, "Missing element with id='setting-remote-instances'"
    # Must be inside the devices panel
    devices_panel = soup.find("div", attrs={"data-tab": "devices"})
    assert devices_panel is not None, "Missing devices panel (data-tab='devices')"
    assert devices_panel.find(id="setting-remote-instances") is not None, (
        "#setting-remote-instances must be inside the devices (Multi-Device) settings panel"
    )


def test_html_sessions_tab_add_remote_instance_btn() -> None:
    """Multi-Device tab must contain an #add-remote-instance-btn button to add remote instances."""
    soup = _SOUP
    el = soup.find(id="add-remote-instance-btn")
    assert el is not None, "Missing element with id='add-remote-instance-btn'"
    assert el.name == "button", (
        f"#add-remote-instance-btn must be a <button>, got: {el.name}"
    )
    # Must be inside the devices panel
    devices_panel = soup.find("div", attrs={"data-tab": "devices"})
    assert devices_panel is not None, "Missing devices panel (data-tab='devices')"
    assert devices_panel.find(id="add-remote-instance-btn") is not None, (
        "#add-remote-instance-btn must be inside the devices (Multi-Device) settings panel"
    )


# ============================================================
# Delete session template (task: customizable delete command)
# ============================================================


def test_html_delete_template_textarea_exists() -> None:
    """New Session (Commands) panel must contain #setting-delete-template textarea."""
    soup = _SOUP
    dialog = soup.find(id="settings-dialog")
    assert dialog is not None, "Missing #settings-dialog"
    new_session_panel = dialog.find(
        class_="settings-panel", attrs={"data-tab": "new-session"}
    )
    assert new_session_panel is not None, "Missing new-session settings-panel"
    textarea = new_session_panel.find("textarea", id="setting-delete-template")
    assert textarea is not None, (
        "Missing <textarea id='setting-delete-template'> inside new-session panel"
    )
    classes = textarea.get("class") or []
    assert "settings-textarea" in classes, (
        f"#setting-delete-template must have class 'settings-textarea', has: {classes}"
    )


def test_html_delete_template_textarea_placeholder() -> None:
    """#setting-delete-template must have placeholder 'tmux kill-session -t {name}'."""
    soup = _SOUP
    textarea = soup.find("textarea", id="setting-delete-template")
    assert textarea is not None, "Missing #setting-delete-template textarea"
    placeholder = textarea.get("placeholder")
    assert placeholder == "tmux kill-session -t {name}", (
        f"#setting-delete-template placeholder must be 'tmux kill-session -t {{name}}', "
        f"got: {placeholder!r}"
    )


def test_html_delete_template_textarea_rows() -> None:
    """#setting-delete-template textarea must have rows=3."""
    soup = _SOUP
    textarea = soup.find("textarea", id="setting-delete-template")
    assert textarea is not None, "Missing #setting-delete-template textarea"
    rows = textarea.get("rows")
    assert rows == "3", f"#setting-delete-template must have rows='3', got: {rows!r}"


def test_html_delete_template_reset_button_exists() -> None:
    """New Session (Commands) panel must contain #setting-delete-template-reset button."""
    soup = _SOUP
    dialog = soup.find(id="settings-dialog")
    assert dialog is not None, "Missing #settings-dialog"
    new_session_panel = dialog.find(
        class_="settings-panel", attrs={"data-tab": "new-session"}
    )
    assert new_session_panel is not None, "Missing new-session settings-panel"
    reset_btn = new_session_panel.find(id="setting-delete-template-reset")
    assert reset_btn is not None, (
        "Missing #setting-delete-template-reset inside new-session panel"
    )
    classes = reset_btn.get("class") or []
    assert "settings-action-btn" in classes, (
        f"#setting-delete-template-reset must have class 'settings-action-btn', has: {classes}"
    )


def test_html_commands_tab_label() -> None:
    """The new-session tab button must be labeled 'Commands' (not 'New Session')."""
    soup = _SOUP
    dialog = soup.find(id="settings-dialog")
    assert dialog is not None, "Missing #settings-dialog"
    tabs_container = dialog.find("nav", class_="settings-tabs")
    assert tabs_container is not None, "Missing nav.settings-tabs"
    tab_btn = tabs_container.find("button", attrs={"data-tab": "new-session"})
    assert tab_btn is not None, "Missing tab button with data-tab='new-session'"
    label = tab_btn.get_text(strip=True)
    assert label == "Commands", (
        f"Tab button data-tab='new-session' must be labeled 'Commands', got: {label!r}"
    )


def test_html_new_session_tab_controls_with_delete() -> None:
    """New Session (Commands) tab must contain create template, delete template, and both reset buttons."""
    soup = _SOUP
    for id_ in (
        "setting-template",
        "setting-template-reset",
        "setting-delete-template",
        "setting-delete-template-reset",
    ):
        assert soup.find(id=id_), f"Missing element with id='{id_}'"


def test_html_view_mode_button_exists() -> None:
    """#view-mode-btn must exist in the overview header for cycling view modes."""
    soup = _SOUP
    btn = soup.find(id="view-mode-btn")
    assert btn is not None, (
        "Missing element with id='view-mode-btn' (view mode toggle button)"
    )
    # Must be inside the overview header area
    overview = soup.find(id="view-overview")
    assert overview is not None, "Missing #view-overview"
    assert overview.find(id="view-mode-btn") is not None, (
        "#view-mode-btn must be inside #view-overview header"
    )


# ============================================================
# Multi-Device tab (settings UI reorganization)
# ============================================================


def test_html_devices_tab_button_exists() -> None:
    """Settings dialog must contain a tab button with data-tab='devices' labeled 'Multi-Device'."""
    soup = _SOUP
    dialog = soup.find(id="settings-dialog")
    assert dialog is not None, "Missing #settings-dialog"
    tabs_container = dialog.find("nav", class_="settings-tabs")
    assert tabs_container is not None, "Missing nav.settings-tabs"
    tab = tabs_container.find("button", attrs={"data-tab": "devices"})
    assert tab is not None, (
        "Missing tab button with data-tab='devices' in settings-tabs"
    )
    tab_classes = tab.get("class") or []
    assert "settings-tab" in tab_classes, (
        f"devices tab button must have class 'settings-tab', has: {tab_classes}"
    )


def test_html_devices_panel_exists() -> None:
    """A settings-panel with data-tab='devices' must exist inside #settings-dialog."""
    soup = _SOUP
    dialog = soup.find(id="settings-dialog")
    assert dialog is not None, "Missing #settings-dialog"
    panel = dialog.find(class_="settings-panel", attrs={"data-tab": "devices"})
    assert panel is not None, (
        "Missing settings-panel[data-tab='devices'] (Multi-Device tab panel)"
    )


def test_html_devices_panel_has_enable_checkbox() -> None:
    """Multi-Device tab panel must contain #setting-multi-device-enabled checkbox."""
    soup = _SOUP
    devices_panel = soup.find("div", attrs={"data-tab": "devices"})
    assert devices_panel is not None, "Missing devices panel (data-tab='devices')"
    el = devices_panel.find(id="setting-multi-device-enabled")
    assert el is not None, "Missing #setting-multi-device-enabled inside devices panel"
    assert el.name == "input", (
        f"#setting-multi-device-enabled must be an <input>, got: {el.name}"
    )
    assert el.get("type") == "checkbox", (
        f"#setting-multi-device-enabled must be type='checkbox', got: {el.get('type')}"
    )


def test_html_devices_panel_has_device_name() -> None:
    """Display tab panel must contain #setting-device-name text input."""
    soup = _SOUP
    display_panel = soup.find("div", attrs={"data-tab": "display"})
    assert display_panel is not None, "Missing display panel (data-tab='display')"
    el = display_panel.find(id="setting-device-name")
    assert el is not None, "Missing #setting-device-name inside display panel"


def test_html_devices_panel_has_remote_instances() -> None:
    """Multi-Device tab panel must contain #setting-remote-instances container."""
    soup = _SOUP
    devices_panel = soup.find("div", attrs={"data-tab": "devices"})
    assert devices_panel is not None, "Missing devices panel (data-tab='devices')"
    el = devices_panel.find(id="setting-remote-instances")
    assert el is not None, "Missing #setting-remote-instances inside devices panel"


def test_html_devices_panel_has_add_remote_btn() -> None:
    """Multi-Device tab panel must contain #add-remote-instance-btn button."""
    soup = _SOUP
    devices_panel = soup.find("div", attrs={"data-tab": "devices"})
    assert devices_panel is not None, "Missing devices panel (data-tab='devices')"
    btn = devices_panel.find(id="add-remote-instance-btn")
    assert btn is not None, "Missing #add-remote-instance-btn inside devices panel"
    assert btn.name == "button", (
        f"#add-remote-instance-btn must be a <button>, got: {btn.name}"
    )


def test_html_devices_panel_has_view_mode() -> None:
    """Multi-Device tab panel must contain #setting-view-mode select."""
    soup = _SOUP
    devices_panel = soup.find("div", attrs={"data-tab": "devices"})
    assert devices_panel is not None, "Missing devices panel (data-tab='devices')"
    el = devices_panel.find(id="setting-view-mode")
    assert el is not None, "Missing #setting-view-mode inside devices panel"
    assert el.name == "select", f"#setting-view-mode must be a <select>, got: {el.name}"


def test_html_display_panel_no_view_mode() -> None:
    """Display panel must NOT contain #setting-view-mode (moved to Multi-Device tab)."""
    soup = _SOUP
    display_panel = soup.find("div", attrs={"data-tab": "display"})
    assert display_panel is not None, "Missing display panel"
    el = display_panel.find(id="setting-view-mode")
    assert el is None, (
        "#setting-view-mode must NOT be in the display panel (moved to Multi-Device tab)"
    )


def test_html_display_panel_no_view_scope() -> None:
    """Display panel must NOT contain #setting-view-scope (moved to Multi-Device tab)."""
    soup = _SOUP
    display_panel = soup.find("div", attrs={"data-tab": "display"})
    assert display_panel is not None, "Missing display panel"
    el = display_panel.find(id="setting-view-scope")
    assert el is None, (
        "#setting-view-scope must NOT be in the display panel (moved to Multi-Device tab)"
    )


# ============================================================
# Clickable URLs — xterm-addon-web-links (task: clickable URLs)
# ============================================================


def read_html() -> str:
    """Read raw HTML content of index.html."""
    return HTML_PATH.read_text()


def test_html_loads_web_links_addon() -> None:
    """index.html must load the xterm-addon-web-links CDN script."""
    html = read_html()
    assert "web-links" in html.lower() or "weblinks" in html.lower(), (
        "Must load xterm-addon-web-links from CDN"
    )

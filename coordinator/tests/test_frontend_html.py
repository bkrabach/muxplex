"""Tests for frontend/index.html — verifies presence of all required DOM elements."""

import pathlib

from bs4 import BeautifulSoup

HTML_PATH = pathlib.Path(__file__).parent.parent.parent / "frontend" / "index.html"

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
    """id=back-btn, expanded-session-name, palette-trigger, reconnect-overlay."""
    soup = _SOUP
    for id_ in (
        "back-btn",
        "expanded-session-name",
        "palette-trigger",
        "reconnect-overlay",
    ):
        assert soup.find(id=id_), f"Missing element with id='{id_}'"


def test_html_command_palette() -> None:
    """id=command-palette, palette-input, palette-list, palette-backdrop."""
    soup = _SOUP
    for id_ in ("command-palette", "palette-input", "palette-list", "palette-backdrop"):
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
    header_children_ids = [el.get("id") for el in header.children if hasattr(el, "get")]
    header_children_ids = [i for i in header_children_ids if i]
    assert "back-btn" in header_children_ids, "#back-btn must be in expanded-header"
    assert "sidebar-toggle-btn" in header_children_ids, "#sidebar-toggle-btn must be in expanded-header"
    assert "expanded-session-name" in header_children_ids, "#expanded-session-name must be in expanded-header"
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
    assert collapse_btn is not None, "Missing #sidebar-collapse-btn inside .sidebar-header"
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
        ("palette-trigger", "palette-trigger", "needs border and hover styles"),
        (
            "expanded-session-name",
            "expanded-session-name",
            "needs text-overflow:ellipsis",
        ),
        ("session-pill-label", "session-pill__label", "needs max-width truncation"),
        ("session-pill-bell", "session-pill__bell", "needs amber var(--bell) color"),
    ]
    for el_id, expected_class, reason in cases:
        el = soup.find(id=el_id)
        assert el is not None, f"#{el_id} not found in HTML"
        classes = el.get("class") or []
        assert expected_class in classes, (
            f"#{el_id} is missing class '{expected_class}' — {reason}. Has: {classes}"
        )

"""
Tests for the cache-busting version suffix on static asset URLs served by index_page().

Verifies that GET / (the main dashboard) injects ?v=<version> on every
<script src="…"> and <link href="…"> URL so browsers pick up new code
immediately after each release, rather than serving stale JS/CSS from
the HTTP cache.
"""

import importlib.metadata

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from muxplex.main import app


# ---------------------------------------------------------------------------
# Shared fixtures (mirror test_api.py setup so tests run cleanly in isolation)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_startup_and_state(tmp_path, monkeypatch):
    """Redirect state/PID files to tmp_path and stub out long-running startup tasks."""
    tmp_state_dir = tmp_path / "state"
    tmp_state_path = tmp_state_dir / "state.json"
    monkeypatch.setattr("muxplex.state.STATE_DIR", tmp_state_dir)
    monkeypatch.setattr("muxplex.state.STATE_PATH", tmp_state_path)

    tmp_pid_dir = tmp_path / "ttyd"
    tmp_pid_path = tmp_pid_dir / "ttyd.pid"
    monkeypatch.setattr("muxplex.ttyd.TTYD_PID_DIR", tmp_pid_dir)
    monkeypatch.setattr("muxplex.ttyd.TTYD_PID_PATH", tmp_pid_path)

    async def _mock_kill_orphan():
        return False

    monkeypatch.setattr("muxplex.main.kill_orphan_ttyd", _mock_kill_orphan)

    async def noop_poll_loop() -> None:
        pass

    monkeypatch.setattr("muxplex.main._poll_loop", noop_poll_loop)


@pytest.fixture(autouse=True)
def reset_federation_cache():
    """Clear _federation_cache before and after each test."""
    import muxplex.main as main_mod

    main_mod._federation_cache.clear()
    yield
    main_mod._federation_cache.clear()


@pytest.fixture
def client(monkeypatch):
    """Authenticated TestClient with the app lifespan active."""
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")
    with TestClient(app) as c:
        from muxplex.auth import create_session_cookie
        from muxplex.main import _auth_secret, _auth_ttl

        cookie = create_session_cookie(_auth_secret, _auth_ttl)
        c.cookies.set("muxplex_session", cookie)
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_index_soup(client) -> BeautifulSoup:
    response = client.get("/")
    assert response.status_code == 200, f"GET / returned {response.status_code}"
    return BeautifulSoup(response.text, "html.parser")


# ---------------------------------------------------------------------------
# Test 1 — every script src and link href carries the version suffix
# ---------------------------------------------------------------------------


def test_index_all_asset_urls_have_version_suffix(client):
    """GET / must inject ?v=<version> on every <script src> and <link href> asset URL.

    Regression guard for the "is the user seeing stale JS?" investigation:
    verifies that the standard HTTP cache is busted on every release by
    appending a version query parameter to each static asset reference.
    """
    version = importlib.metadata.version("muxplex")
    soup = _get_index_soup(client)

    # All <script src="…"> tags
    script_tags = soup.find_all("script", src=True)
    assert len(script_tags) >= 7, (
        f"Expected at least 7 <script src> tags, found {len(script_tags)}"
    )
    for tag in script_tags:
        src = tag["src"]
        assert f"?v={version}" in src, (
            f"<script src> missing ?v={version} suffix: {src!r}"
        )

    # All <link href="…"> tags
    link_tags = soup.find_all("link", href=True)
    assert len(link_tags) >= 1, "Expected at least one <link href> tag"
    for tag in link_tags:
        href = tag["href"]
        assert f"?v={version}" in href, (
            f"<link href> missing ?v={version} suffix: {href!r}"
        )


# ---------------------------------------------------------------------------
# Test 2 — vendor scripts are individually versioned (not just app.js)
# ---------------------------------------------------------------------------


def test_index_vendor_scripts_each_versioned(client):
    """All five vendor JS bundles must carry the version suffix, not just app.js.

    The browser-tester on spark-1 observed bare vendor URLs.  This test
    ensures that xterm.js and its addons are cache-busted alongside the
    first-party scripts.
    """
    version = importlib.metadata.version("muxplex")
    soup = _get_index_soup(client)

    script_srcs = [tag["src"] for tag in soup.find_all("script", src=True)]

    expected_versioned = [
        f"/vendor/xterm.js?v={version}",
        f"/vendor/xterm-addon-fit.js?v={version}",
        f"/vendor/xterm-addon-web-links.js?v={version}",
        f"/vendor/xterm-addon-search.js?v={version}",
        f"/vendor/addon-image.js?v={version}",
        f"/app.js?v={version}",
        f"/terminal.js?v={version}",
    ]
    for expected in expected_versioned:
        assert expected in script_srcs, (
            f"Expected versioned script {expected!r}; found srcs: {script_srcs}"
        )


# ---------------------------------------------------------------------------
# Test 3 — versioned asset URLs still resolve to the actual static files
# ---------------------------------------------------------------------------


def test_versioned_asset_url_resolves_to_static_file(client):
    """GET /app.js?v=<version> must return HTTP 200 (static handler ignores query string).

    Sanity check: adding the version suffix must not break asset loading.
    Starlette's StaticFiles handler ignores query parameters when looking up
    files on disk, so the versioned URL must serve identically to the bare URL.
    """
    version = importlib.metadata.version("muxplex")

    # First-party assets
    for path in ("/app.js", "/terminal.js", "/style.css"):
        url = f"{path}?v={version}"
        resp = client.get(url)
        assert resp.status_code == 200, (
            f"Versioned URL {url!r} returned {resp.status_code}, expected 200"
        )

    # Vendor asset
    vendor_url = f"/vendor/xterm.js?v={version}"
    resp = client.get(vendor_url)
    assert resp.status_code == 200, (
        f"Versioned vendor URL {vendor_url!r} returned {resp.status_code}, expected 200"
    )

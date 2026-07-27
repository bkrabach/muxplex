"""Contract test for muxplex-client: drives MuxplexClient/AsyncMuxplexClient
against the REAL FastAPI app in-process via httpx.ASGITransport.

WHY THIS TEST LIVES HERE (in the server's own suite, not muxplex-client's own
tests/): this is the mechanism that makes shipping muxplex-client as a
second, independently-versioned distribution acceptable at all -- see
../../muxplex-client-design.md §1/§2/§7. A server PR that renames a field the
client parses, or drifts a mirrored constant, turns this test red in the SAME
PR that made the change -- not one release later, in a different repo, after
a user hits it in production.

It runs under this suite's existing safety rails (SETTINGS_PATH redirect,
port-killer neutering -- see tests/conftest.py) and under `make test` (the
DTU). It must never run on a host serving a live muxplex; the conftest.py
guard enforces that.

`muxplex-client` is a dev-only, workspace-editable dependency of THIS
project (see pyproject.toml's `[dependency-groups]`/`[tool.uv.sources]`) --
never a runtime dependency of the `muxplex` server package itself.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient as ASGITestClient

from muxplex.bells import needs_attention as server_needs_attention
from muxplex.main import app
from muxplex.sessions import DEFAULT_CAPTURE_LINES as SERVER_DEFAULT_CAPTURE_LINES
from muxplex.sessions import MAX_CAPTURE_LINES as SERVER_MAX_CAPTURE_LINES
from muxplex.terminal_input import ALLOWED_KEYS as SERVER_ALLOWED_KEYS

from muxplex_client import (
    AsyncMuxplexClient,
    AuthError,
    Bell,
    InputForbidden,
    MuxplexClient,
    SessionNotFound,
)
from muxplex_client.constants import (
    DEFAULT_CAPTURE_LINES,
    KNOWN_KEYS,
    MAX_CAPTURE_LINES,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _redirect_state(tmp_path, monkeypatch):
    """SETTINGS_PATH is already redirected globally by conftest.py's autouse
    `_isolate_settings_path`. STATE_PATH is not -- redirect it here the same
    way test_api.py's `patch_startup_and_state` fixture does, so nothing in
    this test touches a real user's state.json.
    """
    state_dir = tmp_path / "state"
    monkeypatch.setattr("muxplex.state.STATE_DIR", state_dir)
    monkeypatch.setattr("muxplex.state.STATE_PATH", state_dir / "state.json")


@pytest.fixture
def seeded_session(monkeypatch):
    """Fake one tmux session, without touching real tmux.

    Same monkeypatch-the-cache pattern test_api.py uses throughout --
    `httpx.ASGITransport` does not run the app's lifespan, so the real poll
    loop never runs and these module-level caches would otherwise stay empty.
    """
    import muxplex.main as main_mod

    name = "contract-test"
    snapshot_text = "$ echo hi\nhi\n$ "
    monkeypatch.setattr(main_mod, "get_session_list", lambda: [name])
    monkeypatch.setattr(main_mod, "get_snapshots", lambda: {name: snapshot_text})
    monkeypatch.setattr(main_mod, "get_session_activity", lambda: {name: 1700000000.0})

    async def _fake_capture_pane(
        session_name: str, lines: int = SERVER_DEFAULT_CAPTURE_LINES
    ):
        return snapshot_text

    monkeypatch.setattr(main_mod, "capture_pane", _fake_capture_pane)
    return name


@pytest.fixture
def no_sessions(monkeypatch):
    """Explicitly empty the session cache (fail-closed 404 path)."""
    import muxplex.main as main_mod

    monkeypatch.setattr(main_mod, "get_session_list", lambda: [])
    monkeypatch.setattr(main_mod, "get_snapshots", lambda: {})
    monkeypatch.setattr(main_mod, "get_session_activity", lambda: {})


def _sync_asgi_client(
    *, client_addr: tuple[str, int] = ("127.0.0.1", 12345)
) -> httpx.Client:
    """Build a synchronous httpx.Client driving the real app in-process.

    httpx's `ASGITransport` is async-only (`AsyncBaseTransport`), so it
    cannot back a synchronous `httpx.Client` -- see
    `httpx.ASGITransport.__mro__`. Starlette's `TestClient` **is** an
    `httpx.Client` subclass (it bridges sync calls to the async app via an
    internal portal), so it is the correct sync transport for driving
    `MuxplexClient` against the app with no network, no port, no live host.

    Deliberately NOT entered as a context manager (`with TestClient(...) as
    c:`): doing so runs the real ASGI lifespan (startup/shutdown), which for
    this app means the real background poll loop and `kill_orphan_ttyd()` --
    exactly the live-process side effects this in-process contract test
    must never trigger, and which hung indefinitely in the DTU (no real
    tmux-adjacent environment for it to settle in). Used bare, `TestClient`
    spins an ephemeral per-request portal with no lifespan involved, which
    is exactly the "no network, no port, no live host" shape this test
    needs -- confirmed against a minimal repro before landing this fixture.

    `client_addr` sets the ASGI scope's client address; ("127.0.0.1", ...)
    triggers `AuthMiddleware`'s localhost bypass (the default for every test
    here except the explicit non-localhost 401 test below).
    """
    return ASGITestClient(
        app,
        base_url="http://testserver",
        headers={"Accept": "application/json"},
        follow_redirects=False,
        client=client_addr,
    )


@pytest.fixture
def sync_client():
    """MuxplexClient wired to the real app via a sync ASGI-bridging
    transport -- no network, no port, no live host. `client=...` is exactly
    the injection seam the library's design reserves for this
    (muxplex-client-design.md §8)."""
    raw = _sync_asgi_client()
    client = MuxplexClient("http://testserver", client=raw)
    yield client
    client.close()


@pytest.fixture
def raw_http():
    """Undecorated sync client over the same app, for asserting the
    client's parsed fields against the server's actual raw JSON.

    Bare (not entered as a context manager) -- see `_sync_asgi_client`'s
    docstring for why.
    """
    c = _sync_asgi_client()
    yield c
    c.close()


@pytest.fixture
async def async_client():
    raw = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )
    client = AsyncMuxplexClient("http://testserver", client=raw)
    yield client
    await client.aclose()


# ---------------------------------------------------------------------------
# Field-presence: every field the client parses must exist on the real
# response, for every GET endpoint the client models.
# ---------------------------------------------------------------------------


def test_instance_info_fields_present(sync_client, raw_http):
    info = sync_client.instance_info()
    raw = raw_http.get("/api/instance-info").json()

    assert info.name == raw["name"]
    assert info.device_id == raw["device_id"]
    assert info.version == raw["version"]
    assert info.federation_enabled == raw["federation_enabled"]
    assert info.tmux_socket_dir == raw["tmux_socket_dir"]
    assert info.bell_hook_armed == raw["bell_hook_armed"]
    assert info.raw == raw


def test_sessions_fields_present(sync_client, raw_http, seeded_session):
    sessions = sync_client.sessions()
    raw = raw_http.get("/api/sessions").json()

    assert len(sessions) == len(raw) == 1
    parsed, raw_item = sessions[0], raw[0]
    assert parsed.name == raw_item["name"]
    assert parsed.snapshot == raw_item["snapshot"]
    assert parsed.last_activity_at == raw_item["last_activity_at"]
    assert parsed.bell.unseen_count == raw_item["bell"]["unseen_count"]
    assert parsed.bell.last_fired_at == raw_item["bell"]["last_fired_at"]
    assert parsed.bell.seen_at == raw_item["bell"]["seen_at"]


def test_session_snapshot_fields_present(sync_client, raw_http, seeded_session):
    snap = sync_client.session(seeded_session, lines=10)
    raw = raw_http.get(f"/api/sessions/{seeded_session}", params={"lines": 10}).json()

    assert snap.name == raw["name"]
    assert snap.snapshot == raw["snapshot"]
    assert snap.lines == raw["lines"]
    assert snap.last_activity_at == raw["last_activity_at"]
    assert snap.bell.unseen_count == raw["bell"]["unseen_count"]


def test_view_fields_present(sync_client, raw_http, seeded_session):
    result = sync_client.view()
    raw = raw_http.get("/api/view").json()

    assert result.view == raw["view"]
    assert list(result.views) == raw["views"]
    assert result.sort == raw["sort"]
    assert len(result.sessions) == len(raw["sessions"]) == 1
    parsed, raw_item = result.sessions[0], raw["sessions"][0]
    assert parsed.name == raw_item["name"]
    assert parsed.active == raw_item["active"]
    assert parsed.needs_attention == raw_item["needs_attention"]
    assert parsed.last_activity_at == raw_item["last_activity_at"]


def test_view_attention_sort_accepted(sync_client):
    """?sort=attention is a real, documented value -- exercise it too."""
    result = sync_client.view(sort="attention")
    assert result.sort == "attention"


def test_state_fields_present(sync_client, raw_http):
    state = sync_client.state()
    raw = raw_http.get("/api/state").json()

    assert state.active_session == raw["active_session"]
    assert state.active_view == (raw["active_view"] or "all")
    assert state.settings_updated_at == raw["settings_updated_at"]
    assert state.raw == raw


def test_settings_fields_present(sync_client, raw_http):
    settings = sync_client.settings()
    raw = raw_http.get("/api/settings").json()

    assert settings.sort_order == raw["sort_order"]
    assert settings.hidden_sessions == frozenset(raw["hidden_sessions"])
    assert len(settings.views) == len(raw["views"])
    assert settings.raw == raw


# ---------------------------------------------------------------------------
# Mirrored constants -- the drift hazard AGENTS.md names, made CI-enforced.
# ---------------------------------------------------------------------------


def test_known_keys_matches_server_allowed_keys():
    assert KNOWN_KEYS == SERVER_ALLOWED_KEYS


def test_max_capture_lines_matches_server():
    assert MAX_CAPTURE_LINES == SERVER_MAX_CAPTURE_LINES


def test_default_capture_lines_matches_server():
    assert DEFAULT_CAPTURE_LINES == SERVER_DEFAULT_CAPTURE_LINES


# ---------------------------------------------------------------------------
# Bell.needs_attention agreement -- truth table against the server predicate.
# ---------------------------------------------------------------------------


_NEEDS_ATTENTION_TRUTH_TABLE = [
    # (unseen_count, seen_at, last_fired_at)
    (0, None, None),
    (0, 5.0, 10.0),
    (1, None, None),
    (1, None, 5.0),
    (1, 5.0, 10.0),  # fired after seen -> needs attention
    (1, 10.0, 5.0),  # fired before seen -> does not
    (1, 5.0, 5.0),  # fired == seen -> does not (server: strictly >)
    (1, 5.0, None),  # last_fired_at is None, seen_at is not -> defensive False
]


@pytest.mark.parametrize(
    "unseen_count,seen_at,last_fired_at", _NEEDS_ATTENTION_TRUTH_TABLE
)
def test_needs_attention_agrees_with_server(unseen_count, seen_at, last_fired_at):
    server_bell = {
        "unseen_count": unseen_count,
        "seen_at": seen_at,
        "last_fired_at": last_fired_at,
    }
    client_bell = Bell(
        unseen_count=unseen_count, seen_at=seen_at, last_fired_at=last_fired_at
    )

    server_result = server_needs_attention(server_bell)
    client_result = client_bell.needs_attention

    assert client_result == server_result, (
        f"Bell.needs_attention diverged from server bells.needs_attention for "
        f"{server_bell!r}: client={client_result!r} server={server_result!r}"
    )


# ---------------------------------------------------------------------------
# Error mapping, exercised end-to-end against the real app.
# ---------------------------------------------------------------------------


def test_session_not_found_maps_to_session_not_found_error(sync_client, no_sessions):
    with pytest.raises(SessionNotFound) as exc_info:
        sync_client.session("ghost")
    assert exc_info.value.name == "ghost"


def test_connect_not_found_maps_to_session_not_found_error(sync_client, no_sessions):
    with pytest.raises(SessionNotFound):
        sync_client.connect("ghost")


def test_send_input_disabled_maps_to_input_forbidden_not_auth_error(sync_client):
    """settings.input_enabled defaults to False -- the fence fires before any
    subprocess call or existence check, so this is a real, deterministic 403
    with no need for a real tmux session. This is the exact InputForbidden-
    not-AuthError distinction the design calls out (muxplex-client-design.md
    §8): a disabled/non-allowlisted target is an operator fence, not a
    rejected credential.
    """
    with pytest.raises(InputForbidden) as exc_info:
        sync_client.send_input("anything", text="echo hi", enter=True)
    assert not isinstance(exc_info.value, AuthError)
    assert exc_info.value.name == "anything"


def test_no_credential_non_localhost_maps_to_auth_error():
    """A non-localhost caller with no credential must get 401 -> AuthError.

    Every other test in this file uses a ("127.0.0.1", ...) client address,
    which triggers `AuthMiddleware`'s localhost bypass (see
    `_sync_asgi_client`). This test deliberately picks a non-localhost
    address so the real auth-rejection path is exercised too.
    """
    raw = _sync_asgi_client(client_addr=("203.0.113.5", 12345))
    client = MuxplexClient("http://testserver", client=raw)
    try:
        with pytest.raises(AuthError):
            client.sessions()
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Async client parity -- same app, same transport shape, `await`-driven.
# ---------------------------------------------------------------------------


async def test_async_client_sessions_matches_sync(async_client, seeded_session):
    sessions = await async_client.sessions()
    assert len(sessions) == 1
    assert sessions[0].name == seeded_session


async def test_async_client_instance_info(async_client):
    info = await async_client.instance_info()
    assert info.version  # non-empty; exact value not pinned across releases


# ---------------------------------------------------------------------------
# Version lockstep -- muxplex-client-design.md §2: one vX.Y.Z tag publishes
# both wheels at that version. This is a MANUAL discipline at release time
# (the two pyproject.toml `version` fields are independent strings; nothing
# in the build derives one from the other) -- this test is the same-PR
# tripwire for that manual step being forgotten, the same shape of guard the
# mirrored-constant assertions above already provide for
# KNOWN_KEYS/MAX_CAPTURE_LINES/etc.
# ---------------------------------------------------------------------------


def test_client_version_matches_server_version():
    """`client/pyproject.toml`'s version must equal the repo root's.

    Lockstep version is a deliberate design decision (muxplex-client-design.md
    §2), not a build-system guarantee: `uv build --all-packages` will happily
    publish two DIFFERENT version numbers from one tag if a release bumps one
    pyproject.toml and forgets the other. This turns that omission into a
    failing test in the SAME PR that bumps the version, rather than a
    published PyPI release where the client wheel's version claims to be
    "cut against" a server release it doesn't actually match.
    """
    repo_root = Path(__file__).resolve().parents[2]
    server_toml = tomllib.loads((repo_root / "pyproject.toml").read_text())
    client_toml = tomllib.loads((repo_root / "client" / "pyproject.toml").read_text())
    server_version = server_toml["project"]["version"]
    client_version = client_toml["project"]["version"]
    assert client_version == server_version, (
        f"muxplex-client version ({client_version}) must match muxplex version "
        f"({server_version}) -- see muxplex-client-design.md §2 (lockstep "
        "version). Bump client/pyproject.toml's version to match at release "
        "time."
    )

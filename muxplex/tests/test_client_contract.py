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

import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import tomllib
from starlette.testclient import TestClient as ASGITestClient

from muxplex.bells import needs_attention as server_needs_attention
from muxplex.main import app
from muxplex.sessions import DEFAULT_CAPTURE_LINES as SERVER_DEFAULT_CAPTURE_LINES
from muxplex.sessions import MAX_CAPTURE_LINES as SERVER_MAX_CAPTURE_LINES
from muxplex.terminal_input import ALLOWED_KEYS as SERVER_ALLOWED_KEYS

# `muxplex-client` is only installed when `uv sync` resolves the uv workspace
# (this project's `[dependency-groups]` -- see pyproject.toml and the module
# docstring above). CI's `test-latest-deps` job deliberately installs via a
# bare `uv pip install -e ".[dev]"` with NO workspace involved, on purpose --
# that mirrors exactly what a real `uv tool install muxplex` resolves for an
# end user (see ci.yml's comment on that job), and muxplex-client is NEVER a
# runtime dependency of the muxplex server package. Installing it into that
# job just to make this file collectible would defeat the job's entire
# premise: it exists to catch resolution drift in the SERVER's own
# dependencies against a real user's install, not to test a hybrid
# environment no real user has.
#
# So: skip this whole contract-test module when muxplex_client isn't
# installed, rather than fail collection. This checks ONLY package presence,
# deliberately kept separate from the `from muxplex_client import ...` below:
# if muxplex_client IS installed but a specific name has genuinely drifted
# (the exact regression this file exists to catch -- see module docstring),
# that must surface as a real ImportError everywhere the package is present,
# not get silently absorbed into this skip.
try:
    import muxplex_client as _muxplex_client_probe  # noqa: F401
except ImportError:
    pytest.skip(
        "muxplex_client not installed (expected outside the uv workspace, "
        "e.g. CI's test-latest-deps job -- see comment above)",
        allow_module_level=True,
    )

from muxplex_client import (
    ApiError,
    AsyncMuxplexClient,
    AuthError,
    Bell,
    DestructiveChange,
    InputForbidden,
    MuxplexClient,
    MuxplexError,
    SessionNotFound,
    SessionWaitTimeout,
    SettingsConflict,
)
from muxplex_client.constants import (
    DEFAULT_CAPTURE_LINES,
    KNOWN_KEYS,
    MAX_CAPTURE_LINES,
    SUBPROCESS_TIMEOUT,
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


# ---------------------------------------------------------------------------
# Liveness / auth mode / CA certificate
# ---------------------------------------------------------------------------


def test_health_fields_present(sync_client, raw_http):
    """GET /health -- unauthenticated liveness check; client returns the raw dict."""
    result = sync_client.health()
    raw = raw_http.get("/health").json()

    assert result == raw == {"status": "ok"}


def test_auth_mode_fields_present(sync_client, raw_http):
    """GET /auth/mode -- returns the server's configured auth mode and running user."""
    result = sync_client.auth_mode()
    raw = raw_http.get("/auth/mode").json()

    assert result == raw
    assert "mode" in result
    assert "user" in result


def test_ca_certificate_404_when_not_configured(sync_client):
    """GET /api/ca 404s cleanly (ApiError) when no local CA is configured -- the
    default in this test environment, since get_local_ca_cert_path() resolves
    under the redirected SETTINGS_PATH, where no ca/muxplex-ca.crt exists."""
    with pytest.raises(ApiError) as exc_info:
        sync_client.ca_certificate()

    assert exc_info.value.status == 404


def test_ca_certificate_returns_pem_text(sync_client, monkeypatch):
    """GET /api/ca returns PEM text via the client's _request_text path, not JSON."""
    fake_pem = "-----BEGIN CERTIFICATE-----\nFAKEDATA\n-----END CERTIFICATE-----\n"
    monkeypatch.setattr(
        "muxplex.main._read_local_ca_cert_bytes", lambda: fake_pem.encode("ascii")
    )

    result = sync_client.ca_certificate()

    assert result == fake_pem


# ---------------------------------------------------------------------------
# Session lifecycle extras: delete-current, SessionWaitTimeout dual-inheritance
# ---------------------------------------------------------------------------


def test_delete_current_session_kills_ttyd_and_clears_active(
    sync_client, raw_http, monkeypatch
):
    """DELETE /api/sessions/current kills ttyd and clears active_session."""
    import muxplex.main as main_mod
    from muxplex.state import save_state

    save_state(
        {
            "active_session": "alpha",
            "active_remote_id": None,
            "active_view": "all",
            "session_order": ["alpha"],
            "sessions": {},
            "devices": {},
        }
    )

    kill_called = []

    async def mock_kill():
        kill_called.append(True)
        return True

    monkeypatch.setattr(main_mod, "kill_ttyd", mock_kill)

    sync_client.delete_current_session()

    assert len(kill_called) == 1
    state = raw_http.get("/api/state").json()
    assert state["active_session"] is None


def test_create_session_wait_timeout_raises_session_wait_timeout(
    sync_client, no_sessions, monkeypatch
):
    """create_session(wait=True) raises SessionWaitTimeout when the session never
    appears in the read cache -- catchable as BOTH MuxplexError and builtin
    TimeoutError, since the dual inheritance exists to preserve backward
    compatibility with pre-existing `except TimeoutError` callers (this
    replaced a bare TimeoutError previously raised directly).
    """
    import muxplex.main as main_mod

    async def fake_spawn(name):
        return True, None

    monkeypatch.setattr(main_mod, "spawn_session_command", fake_spawn)

    with pytest.raises(SessionWaitTimeout) as exc_info:
        sync_client.create_session(
            "ghost-timeout", wait=True, timeout=0.05, interval=0.01
        )

    exc = exc_info.value
    assert exc.name == "ghost-timeout"
    assert isinstance(exc, MuxplexError)
    assert isinstance(exc, TimeoutError)


# ---------------------------------------------------------------------------
# PATCH /api/state -- UNSET sentinel distinguishes "omitted" from "explicit None"
# ---------------------------------------------------------------------------


def test_patch_state_only_sends_explicitly_passed_fields(sync_client, raw_http):
    """patch_state(active_view=...) alone must leave active_session untouched --
    the server only applies `model_fields_set`, so UNSET fields must never be
    included in the PATCH body at all (a `null` would clear them)."""
    sync_client.patch_state(active_session="alpha", active_view="all")

    sync_client.patch_state(active_view="my-view")

    state = raw_http.get("/api/state").json()
    assert state["active_session"] == "alpha"
    assert state["active_view"] == "my-view"


def test_patch_state_explicit_none_clears_active_session(sync_client, raw_http):
    """patch_state(active_session=None), passed explicitly, DOES clear the
    field -- distinguishing "explicitly cleared" from "omitted" is exactly
    what the UNSET sentinel (vs. a plain None default) exists to prove."""
    sync_client.patch_state(active_session="alpha", active_view="my-view")

    sync_client.patch_state(active_session=None)

    state = raw_http.get("/api/state").json()
    assert state["active_session"] is None
    assert state["active_view"] == "my-view"  # untouched by the second call


# ---------------------------------------------------------------------------
# PATCH /api/settings -- CAS precondition, apply_settings retry-once, and the
# destructive-write backstop that must NEVER be auto-retried
# ---------------------------------------------------------------------------


def test_update_settings_sends_expected_timestamp_and_succeeds(sync_client, raw_http):
    """PATCH /api/settings with a correct expected_settings_updated_at applies
    the patch and returns the updated settings."""
    state = raw_http.get("/api/state").json()
    ts = state["settings_updated_at"]

    result = sync_client.update_settings(
        {"sort_order": "attention"}, expected_settings_updated_at=ts
    )

    assert result["sort_order"] == "attention"


def test_update_settings_stale_expected_timestamp_raises_settings_conflict(
    sync_client,
):
    """A stale expected_settings_updated_at 409s -> SettingsConflict (not
    DestructiveChange, since this patch never touches views)."""
    with pytest.raises(SettingsConflict) as exc_info:
        sync_client.update_settings(
            {"sort_order": "attention"}, expected_settings_updated_at=-1.0
        )

    assert not isinstance(exc_info.value, DestructiveChange)


def test_apply_settings_retries_once_on_cas_conflict_with_fresh_data(
    sync_client, raw_http
):
    """apply_settings() sends expected_settings_updated_at; on a 409 CAS
    mismatch it re-reads current settings/state, re-applies the caller's
    mutate function to the FRESH data, and retries exactly once -- per
    API_SEMANTICS.md's patchSettingsGuarded reference behavior.

    Simulates a concurrent writer landing between apply_settings' initial
    read and its first PATCH by having *mutate* itself trigger an
    out-of-band raw PATCH the first time it's called (after it has already
    captured the value it was given, but before apply_settings sends its
    own PATCH). This forces the first PATCH attempt's
    expected_settings_updated_at to be stale, so a retry is REQUIRED for
    this test to pass at all -- if the client failed to send
    expected_settings_updated_at (no CAS enforced), the first attempt would
    silently succeed and mutate would only ever be called once.
    """
    seen_sort_orders = []

    def mutate(current):
        seen_sort_orders.append(current["sort_order"])
        if len(seen_sort_orders) == 1:
            # Out-of-band writer: bumps the real settings_updated_at after
            # our `current` snapshot was taken, but before our own PATCH lands.
            resp = raw_http.patch("/api/settings", json={"sort_order": "alphabetical"})
            assert resp.status_code == 200
        return {"sort_order": "attention"}

    result = sync_client.apply_settings(mutate)

    assert seen_sort_orders == ["manual", "alphabetical"], (
        "mutate's second call must see the FRESH re-read, not the original "
        "stale snapshot -- and must be called exactly twice (one retry)"
    )
    assert result["sort_order"] == "attention"


def test_apply_settings_never_retries_destructive_change(sync_client, raw_http):
    """A views collapse (2 views -> 1) 409s with backstop=true ->
    DestructiveChange, raised IMMEDIATELY with no retry -- unlike an
    ordinary SettingsConflict, this must never be auto-retried even against
    fresh data (see DestructiveChange's docstring: a stale write is out of
    date, but a destructive write is wrong, and fresh data doesn't fix
    that). This guard exists because a stale client once destroyed 7 of 8
    views in a single request.
    """
    resp = raw_http.patch(
        "/api/settings",
        json={
            "views": [
                {"name": "v1", "sessions": []},
                {"name": "v2", "sessions": []},
            ]
        },
    )
    assert resp.status_code == 200

    calls = []

    def mutate(current):
        calls.append(len(current["views"]))
        return {"views": [{"name": "v1", "sessions": []}]}  # 2 -> 1: collapse

    with pytest.raises(DestructiveChange) as exc_info:
        sync_client.apply_settings(mutate)

    assert calls == [2]  # mutate called exactly once -- never retried
    assert exc_info.value.counts["before_views"] == 2
    assert exc_info.value.counts["after_views"] == 1


# ---------------------------------------------------------------------------
# Settings sync (federation push/pull)
# ---------------------------------------------------------------------------


def test_settings_sync_fields_present(sync_client, raw_http):
    """GET /api/settings/sync returns syncable settings plus both timestamps."""
    result = sync_client.settings_sync()
    raw = raw_http.get("/api/settings/sync").json()

    assert result == raw
    assert "settings_updated_at" in result
    assert "views_updated_at" in result
    assert "sort_order" in result["settings"]


def test_put_settings_sync_accepts_strictly_newer_timestamp(sync_client):
    """PUT /api/settings/sync with a strictly-newer settings_updated_at
    applies the incoming payload (newer-wins) and returns 200."""
    payload = {
        "settings": {"sort_order": "attention"},
        "settings_updated_at": time.time() + 1000,
    }

    result = sync_client.put_settings_sync(payload)

    assert result["settings"]["sort_order"] == "attention"


def test_put_settings_sync_stale_timestamp_raises_api_error(sync_client):
    """PUT /api/settings/sync with a non-newer settings_updated_at 409s; the
    client maps this to ApiError rather than SettingsConflict, since
    map_status_error's CAS-specific mapping applies ONLY to the exact path
    "/api/settings" (this endpoint's path is "/api/settings/sync")."""
    payload = {"settings": {"sort_order": "attention"}, "settings_updated_at": 0.0}

    with pytest.raises(ApiError) as exc_info:
        sync_client.put_settings_sync(payload)

    assert exc_info.value.status == 409


# ---------------------------------------------------------------------------
# Device heartbeat + hook re-registration
# ---------------------------------------------------------------------------


def test_heartbeat_registers_device(sync_client, raw_http):
    """POST /api/heartbeat registers/updates a device and returns
    {device_id, status: 'ok'}."""
    payload = {
        "device_id": "device-abc",
        "label": "Test Device",
        "viewing_session": None,
        "view_mode": "grid",
        "last_interaction_at": 1700000000.0,
    }

    result = sync_client.heartbeat(payload)

    assert result == {"device_id": "device-abc", "status": "ok"}
    state = raw_http.get("/api/state").json()
    assert "device-abc" in state["devices"]


def test_setup_hooks_returns_ok_true_when_arm_succeeds(sync_client, monkeypatch):
    """POST /api/internal/setup-hooks returns {ok: True} when hook
    registration succeeds -- delegates to the same _arm_bell_hook() the
    poll loop's self-healing retry uses."""

    async def fake_arm():
        return True

    monkeypatch.setattr("muxplex.main._arm_bell_hook", fake_arm)

    assert sync_client.setup_hooks() == {"ok": True}


def test_setup_hooks_returns_ok_false_with_error_when_arm_fails(
    sync_client, monkeypatch
):
    """POST /api/internal/setup-hooks returns {ok: False, error: ...} when
    hook registration fails -- the error message comes from the
    module-level _bell_hook_last_error global _arm_bell_hook() records."""
    import muxplex.main as main_mod

    async def fake_arm():
        main_mod._bell_hook_last_error = "simulated failure"
        return False

    monkeypatch.setattr(main_mod, "_arm_bell_hook", fake_arm)

    assert sync_client.setup_hooks() == {"ok": False, "error": "simulated failure"}


# ---------------------------------------------------------------------------
# Bells
# ---------------------------------------------------------------------------


def test_ring_bell_then_clear_bell_updates_state(sync_client, raw_http):
    """POST .../bell increments unseen_count and stamps last_fired_at; POST
    .../bell/clear resets unseen_count to 0 and stamps seen_at. Neither
    endpoint validates session existence -- both create the state entry."""
    name = "bell-test-session"

    sync_client.ring_bell(name)
    state = raw_http.get("/api/state").json()
    bell = state["sessions"][name]["bell"]
    assert bell["unseen_count"] == 1
    assert bell["last_fired_at"] is not None

    sync_client.clear_bell(name)
    state = raw_http.get("/api/state").json()
    bell = state["sessions"][name]["bell"]
    assert bell["unseen_count"] == 0
    assert bell["seen_at"] is not None


# ---------------------------------------------------------------------------
# Federation
# ---------------------------------------------------------------------------


def test_federation_sessions_local_only_when_no_remotes(
    sync_client, raw_http, seeded_session
):
    """GET /api/federation/sessions with no configured remotes returns just
    the tagged local sessions -- a real session entry (status=None,
    is_session=True)."""
    entries = sync_client.federation_sessions()
    raw = raw_http.get("/api/federation/sessions").json()

    assert len(entries) == len(raw) == 1
    entry, raw_item = entries[0], raw[0]
    assert entry.is_session is True
    assert entry.status is None
    assert entry.name == raw_item["name"] == seeded_session
    assert entry.device_id == raw_item["deviceId"]
    assert entry.session_key == raw_item["sessionKey"]


def test_federation_sessions_status_entries_are_not_sessions(
    sync_client, no_sessions, monkeypatch
):
    """A dead/auth-rejected/empty remote returns an in-band status entry
    (HTTP 200) carrying `status` and NO `name` field -- FederationEntry.
    is_session must be False for every one of them, and nothing may raise.

    `app.state.federation_client` is only populated during the real ASGI
    lifespan (never run by this file's bare TestClient -- see
    `_sync_asgi_client`'s docstring), so it's monkeypatched directly here
    with `raising=False` (the attribute may not exist yet), matching
    conftest.py's own `_isolate_settings_path` convention for patching an
    attribute that might not already be present.
    """
    import json
    from unittest.mock import MagicMock

    import muxplex.settings as settings_mod

    settings_mod.SETTINGS_PATH.write_text(
        json.dumps(
            {
                "device_name": "local",
                "remote_instances": [
                    {
                        "url": "http://contract-test-unreachable.invalid:9",
                        "key": "k1",
                        "name": "unreachable-remote",
                        "device_id": "remote-unreachable",
                    },
                    {
                        "url": "http://contract-test-auth-failed.invalid:9",
                        "key": "k2",
                        "name": "auth-failed-remote",
                        "device_id": "remote-auth-failed",
                    },
                    {
                        "url": "http://contract-test-empty.invalid:9",
                        "key": "k3",
                        "name": "empty-remote",
                        "device_id": "remote-empty",
                    },
                ],
            }
        )
    )

    async def mock_get(url, **kwargs):
        if url.endswith("/api/instance-info"):
            # Version probe: _fetch_remote_version swallows everything and
            # returns None -- simulate its own unreachability too.
            raise httpx.ConnectError("simulated: version probe unreachable")
        if "unreachable" in url:
            raise httpx.ConnectError("simulated unreachable")
        if "auth-failed" in url:
            return httpx.Response(401, request=httpx.Request("GET", url))
        if "empty" in url:
            return httpx.Response(200, json=[], request=httpx.Request("GET", url))
        raise AssertionError(f"test bug: unexpected federation fetch url {url!r}")

    mock_client = MagicMock()
    mock_client.get = mock_get
    # `app` is the same module-level FastAPI instance `raw_http`/`sync_client`
    # both drive (see `_sync_asgi_client`) -- using it directly here avoids a
    # pyright false-positive on `raw_http.app` (the fixture parameter's
    # declared type is the fixture *function*, not its yielded value).
    monkeypatch.setattr(app.state, "federation_client", mock_client, raising=False)

    entries = sync_client.federation_sessions()

    statuses = {e.status for e in entries}
    assert statuses == {"unreachable", "auth_failed", "empty"}
    for entry in entries:
        assert entry.is_session is False
        assert entry.name is None


@pytest.mark.parametrize(
    "method_name",
    [
        "federation_connect",
        "federation_create_session",
        "federation_delete_session",
        "federation_clear_bell",
    ],
)
def test_federation_proxy_404_for_unknown_device_maps_to_session_not_found(
    sync_client, method_name
):
    """Every federation proxy method (connect/create/delete/clear_bell) hits
    its real path+verb and reaches _lookup_remote_by_device_id; with no
    remote_instances configured (this test environment's default), the
    lookup always fails -> 404 -- which map_status_error maps to
    SessionNotFound UNCONDITIONALLY regardless of path, since none of these
    calls pass session_name to `_request`."""
    method = getattr(sync_client, method_name)
    with pytest.raises(SessionNotFound) as exc_info:
        method("no-such-device", "some-session")
    assert "no-such-device" in exc_info.value.detail


def test_generate_federation_key_writes_key_and_returns_it(
    sync_client, monkeypatch, tmp_path
):
    """POST /api/federation/generate-key writes a new key to
    FEDERATION_KEY_PATH and returns {key, path}.

    FEDERATION_KEY_PATH is Path.home()-based and is NOT redirected by
    conftest.py's autouse fixtures (only SETTINGS_PATH is) -- this
    monkeypatch is required, or a real call would overwrite the host's
    actual federation key file. See this file's module docstring /
    AGENTS.md's safety-rails section for the class of incident this guards
    against; the endpoint re-imports FEDERATION_KEY_PATH from
    muxplex.settings on every call, so patching the module attribute here
    takes effect.
    """
    key_path = tmp_path / "generated-federation-key"
    monkeypatch.setattr("muxplex.settings.FEDERATION_KEY_PATH", key_path)

    result = sync_client.generate_federation_key()

    assert result["path"] == str(key_path)
    assert result["key"]
    assert key_path.read_text().strip() == result["key"]


# ---------------------------------------------------------------------------
# MuxplexClient.from_env -- wiring contract between resolve_config() and
# __init__(), not itself an HTTP call
# ---------------------------------------------------------------------------


def test_from_env_wires_resolved_config_into_the_transport():
    """from_env() must apply resolve_config()'s resolved server_url,
    federation_key, and timeout to the underlying httpx.Client -- not just
    stash them on `.config`. `env={}`/`home=<nonexistent>` keep this
    hermetic (never touches the real process environment or
    ~/.config/muxplex), per config.py's own testing guidance. This client
    is never actually used to make a request (httpx.Client is lazy at
    construction), so no real network is ever touched.
    """
    client = MuxplexClient.from_env(
        server_url="https://example.invalid:9443",
        federation_key="test-key-123",
        timeout=1.5,
        env={},
        home=Path("/nonexistent-home-for-contract-test"),
    )
    try:
        assert client.config is not None
        assert client.config.server_url == "https://example.invalid:9443"
        assert client._client.base_url.scheme == "https"
        assert client._client.base_url.host == "example.invalid"
        assert client._client.base_url.port == 9443
        assert client._client.headers["Authorization"] == "Bearer test-key-123"
        assert client._client.timeout.connect == 1.5
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Async parity for the highest-value cases (UNSET sentinel, destructive
# backstop, SessionWaitTimeout dual-inheritance). The async client shares
# _protocol.py with the sync client -- see test_async_client_sessions_
# matches_sync above for why full duplication of every sync test isn't done.
# ---------------------------------------------------------------------------


async def test_async_patch_state_only_sends_explicit_fields(async_client):
    """Async parity for the UNSET sentinel: an omitted field must not be sent."""
    await async_client.patch_state(active_session="alpha", active_view="all")

    await async_client.patch_state(active_view="my-view")

    state = await async_client.state()
    assert state.active_session == "alpha"
    assert state.active_view == "my-view"


async def test_async_apply_settings_never_retries_destructive_change(async_client):
    """Async parity: a destructive views collapse raises DestructiveChange
    immediately, with no retry."""
    await async_client.update_settings(
        {"views": [{"name": "v1", "sessions": []}, {"name": "v2", "sessions": []}]}
    )

    calls = []

    def mutate(current):
        calls.append(len(current["views"]))
        return {"views": [{"name": "v1", "sessions": []}]}

    with pytest.raises(DestructiveChange):
        await async_client.apply_settings(mutate)

    assert calls == [2]


async def test_async_create_session_wait_timeout_raises_session_wait_timeout(
    async_client, no_sessions, monkeypatch
):
    """Async parity: create_session(wait=True) raises SessionWaitTimeout,
    catchable as both MuxplexError and builtin TimeoutError."""
    import muxplex.main as main_mod

    async def fake_spawn(name):
        return True, None

    monkeypatch.setattr(main_mod, "spawn_session_command", fake_spawn)

    with pytest.raises(SessionWaitTimeout) as exc_info:
        await async_client.create_session(
            "ghost-timeout-async", wait=True, timeout=0.05, interval=0.01
        )

    exc = exc_info.value
    assert exc.name == "ghost-timeout-async"
    assert isinstance(exc, MuxplexError)
    assert isinstance(exc, TimeoutError)


# ---------------------------------------------------------------------------
# SUBPROCESS_TIMEOUT -- the per-request HTTP timeout override for the three
# endpoints that ask the server to run an operator-supplied subprocess
# synchronously (create_session/delete_session/connect), proven end-to-end
# against the real app rather than only at the unit-test level (see
# client/tests/test_timeouts.py for the pure, no-app version of these
# assertions). A spy wraps the underlying httpx transport's `request()` to
# record each call's `timeout=` kwarg while still delegating through to the
# real call, so the app is genuinely exercised, not bypassed.
# ---------------------------------------------------------------------------


def _capture_sync_request_timeouts(client: MuxplexClient, monkeypatch) -> list[Any]:
    """Spy on *client*'s underlying transport to record every `timeout=` kwarg."""
    captured: list[Any] = []
    original = client._client.request

    def spy(method, path, **kwargs):
        captured.append(kwargs.get("timeout"))
        return original(method, path, **kwargs)

    monkeypatch.setattr(client._client, "request", spy)
    return captured


def _capture_async_request_timeouts(
    client: AsyncMuxplexClient, monkeypatch
) -> list[Any]:
    """Async counterpart of `_capture_sync_request_timeouts`."""
    captured: list[Any] = []
    original = client._client.request

    async def spy(method, path, **kwargs):
        captured.append(kwargs.get("timeout"))
        return await original(method, path, **kwargs)

    monkeypatch.setattr(client._client, "request", spy)
    return captured


def test_create_session_request_uses_subprocess_timeout(
    sync_client, no_sessions, monkeypatch
):
    """create_session's POST /api/sessions sends SUBPROCESS_TIMEOUT as the per-request HTTP timeout, not the client default."""
    import muxplex.main as main_mod

    async def fake_spawn(name):
        return True, None

    monkeypatch.setattr(main_mod, "spawn_session_command", fake_spawn)
    captured = _capture_sync_request_timeouts(sync_client, monkeypatch)

    sync_client.create_session("contract-timeout-test", wait=False)

    assert captured == [SUBPROCESS_TIMEOUT]


def test_delete_session_request_uses_subprocess_timeout(sync_client, monkeypatch):
    """delete_session's DELETE /api/sessions/{name} sends SUBPROCESS_TIMEOUT as the per-request HTTP timeout."""
    import muxplex.main as main_mod

    monkeypatch.setattr(main_mod, "get_session_list", lambda: ["contract-timeout-test"])

    def mock_run(cmd, **kwargs):
        class _Result:
            returncode = 0
            stderr = ""

        return _Result()

    monkeypatch.setattr(main_mod.subprocess, "run", mock_run)
    captured = _capture_sync_request_timeouts(sync_client, monkeypatch)

    sync_client.delete_session("contract-timeout-test")

    assert captured == [SUBPROCESS_TIMEOUT]


def test_connect_request_uses_subprocess_timeout(
    sync_client, seeded_session, monkeypatch
):
    """connect's POST /api/sessions/{name}/connect sends SUBPROCESS_TIMEOUT as the per-request HTTP timeout."""
    import muxplex.main as main_mod

    async def mock_kill():
        return True

    async def mock_spawn(name):
        return None

    monkeypatch.setattr(main_mod, "kill_ttyd", mock_kill)
    monkeypatch.setattr(main_mod, "spawn_ttyd", mock_spawn)
    captured = _capture_sync_request_timeouts(sync_client, monkeypatch)

    sync_client.connect(seeded_session)

    assert captured == [SUBPROCESS_TIMEOUT]


def test_ordinary_read_does_not_use_subprocess_timeout(sync_client, monkeypatch):
    """An ordinary read (GET /api/sessions) leaves the per-request timeout unset -- proves the fix is scoped to only the three subprocess-backed endpoints."""
    captured = _capture_sync_request_timeouts(sync_client, monkeypatch)

    sync_client.sessions()

    assert captured == [httpx.USE_CLIENT_DEFAULT]


def test_create_session_request_timeout_is_overridable_end_to_end(
    sync_client, no_sessions, monkeypatch
):
    """A caller-supplied request_timeout reaches the real transport, overriding SUBPROCESS_TIMEOUT."""
    import muxplex.main as main_mod

    async def fake_spawn(name):
        return True, None

    monkeypatch.setattr(main_mod, "spawn_session_command", fake_spawn)
    captured = _capture_sync_request_timeouts(sync_client, monkeypatch)

    sync_client.create_session(
        "contract-timeout-override", wait=False, request_timeout=90.0
    )

    assert captured == [90.0]


async def test_async_create_session_request_uses_subprocess_timeout(
    async_client, no_sessions, monkeypatch
):
    """Async parity: create_session's POST /api/sessions sends SUBPROCESS_TIMEOUT as the per-request HTTP timeout."""
    import muxplex.main as main_mod

    async def fake_spawn(name):
        return True, None

    monkeypatch.setattr(main_mod, "spawn_session_command", fake_spawn)
    captured = _capture_async_request_timeouts(async_client, monkeypatch)

    await async_client.create_session("contract-timeout-test-async", wait=False)

    assert captured == [SUBPROCESS_TIMEOUT]


async def test_async_ordinary_read_does_not_use_subprocess_timeout(
    async_client, monkeypatch
):
    """Async parity: an ordinary read (GET /api/sessions) leaves the per-request timeout unset."""
    captured = _capture_async_request_timeouts(async_client, monkeypatch)

    await async_client.sessions()

    assert captured == [httpx.USE_CLIENT_DEFAULT]

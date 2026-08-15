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

from pathlib import Path

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
    FollowupItem,
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

    async def _fake_capture_pane_window(session_name: str, s: int, e: int | None):
        return (100, 24, 50000, snapshot_text)

    monkeypatch.setattr(main_mod, "capture_pane_window", _fake_capture_pane_window)
    return name


@pytest.fixture
def no_sessions(monkeypatch):
    """Explicitly empty the session cache (fail-closed 404 path)."""
    import muxplex.main as main_mod

    monkeypatch.setattr(main_mod, "get_session_list", list)
    monkeypatch.setattr(main_mod, "get_snapshots", dict)
    monkeypatch.setattr(main_mod, "get_session_activity", dict)


# A fixed federation key for this test module only. Every contract-test
# client authenticates with it via `Authorization: Bearer` -- this replaces
# the old "client_addr=127.0.0.1" localhost-bypass default the moment that
# bypass was removed (GHSA-7c6r-fvrh-9qp4; muxplex/auth.py's `dispatch`
# docstring has the full rationale). The `_federation_key` autouse fixture
# below wires this same value into the running app.
_TEST_FEDERATION_KEY = "contract-test-federation-key-not-a-real-secret"


@pytest.fixture(autouse=True)
def _federation_key(monkeypatch):
    """Configure the app's federation key so every client in this module can
    authenticate with `_TEST_FEDERATION_KEY` instead of relying on the
    removed localhost bypass."""
    monkeypatch.setattr("muxplex.main._federation_key", _TEST_FEDERATION_KEY)


def _sync_asgi_client(
    *, client_addr: tuple[str, int] = ("203.0.113.5", 12345)
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

    `client_addr` no longer carries any authentication meaning (the
    localhost bypass it used to trigger is gone -- GHSA-7c6r-fvrh-9qp4); the
    default is a non-localhost address on purpose so nothing here can be
    mistaken for exercising that removed path. Authentication comes from the
    `Authorization: Bearer` header set below, matched against
    `_federation_key` (the autouse fixture above).
    """
    return ASGITestClient(
        app,
        base_url="http://testserver",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {_TEST_FEDERATION_KEY}",
        },
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
    assert info.server_started_at == raw["server_started_at"]
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
    # docs/plans/2026-08-04-auto-views-plan.md §10.1: every session dict carries a resolved
    # `views` list -- the vendored muxplex_client parser must keep parsing
    # it (this is the test that keeps the vendored copy honest).
    assert "views" in raw_item
    assert isinstance(raw_item["views"], list)
    assert parsed.views == tuple(raw_item["views"])
    # BACKLOG.md #7 / docs/API_SEMANTICS.md: created_at is always present
    # (null when tmux reported nothing parseable), same shape as
    # last_activity_at above.
    assert "created_at" in raw_item
    assert parsed.created_at == raw_item["created_at"]


def test_session_snapshot_fields_present(sync_client, raw_http, seeded_session):
    snap = sync_client.session(seeded_session, lines=10)
    raw = raw_http.get(f"/api/sessions/{seeded_session}", params={"lines": 10}).json()

    assert snap.name == raw["name"]
    assert snap.snapshot == raw["snapshot"]
    assert snap.lines == raw["lines"]
    assert snap.last_activity_at == raw["last_activity_at"]
    assert snap.bell.unseen_count == raw["bell"]["unseen_count"]
    # Scrollback-paging additions (docs/plans/2026-08-07-scrollback-paging-plan.md
    # §3.3/§5) -- must round-trip through the vendored client parser too.
    assert "start" in raw
    assert "row_count" in raw
    assert "total" in raw
    assert "has_more" in raw
    assert "saturated" in raw
    assert snap.start == raw["start"]
    assert snap.row_count == raw["row_count"]
    assert snap.total == raw["total"]
    assert snap.has_more == raw["has_more"]
    assert snap.saturated == raw["saturated"]


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
# Item D -- create_session(command_id=), delete_session(force=),
# list_session_commands(), and the follow-up queue methods, all driven
# against the real ASGI app.
# ---------------------------------------------------------------------------


def _mock_subprocess_shell(monkeypatch, module_path: str = "tmux_kit.spawn"):
    """Mock `asyncio.create_subprocess_shell` so create_session's spawn
    path never touches a real shell/tmux.

    The spawn body lives in `tmux_kit.spawn` since the S2 extraction
    (`sessions.spawn_session_command()` resolves the template and
    delegates), so that module's asyncio binding is the seam."""
    from unittest.mock import AsyncMock, MagicMock

    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0
    monkeypatch.setattr(
        f"{module_path}.asyncio.create_subprocess_shell",
        AsyncMock(return_value=mock_proc),
    )


def test_create_session_command_id_reaches_server(raw_http, monkeypatch):
    """command_id="default" round-trips and the response echoes it."""
    monkeypatch.setattr("muxplex.main.get_session_list", list)
    _mock_subprocess_shell(monkeypatch)

    raw = raw_http.post(
        "/api/sessions", json={"name": "contract-test-cmd", "command_id": "default"}
    )
    assert raw.status_code == 200
    assert raw.json()["command_id"] == "default"


def test_create_session_without_command_id_sends_no_key(sync_client, monkeypatch):
    """The byte-identity claim: create_session(command_id=None) must never
    send a `command_id` key at all, not even `null`."""
    monkeypatch.setattr("muxplex.main.get_session_list", list)
    _mock_subprocess_shell(monkeypatch)

    captured: dict = {}
    orig_request = sync_client._client.request

    def spy(method, path, **kwargs):
        if path == "/api/sessions":
            captured.update(kwargs)
        return orig_request(method, path, **kwargs)

    monkeypatch.setattr(sync_client._client, "request", spy)

    sync_client.create_session("contract-test-nokey", wait=False)
    assert "json" in captured
    assert "command_id" not in captured["json"]
    assert captured["json"] == {"name": "contract-test-nokey"}


def test_delete_session_force_reaches_server(sync_client, raw_http, monkeypatch):
    """?force=true reaches the server and substitutes the default pair
    when the recorded command_id no longer resolves."""
    from unittest.mock import MagicMock

    name = "contract-test-force"
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: [name])
    monkeypatch.setattr(
        "muxplex.main.get_created_with", lambda manifest, n: "vanished-pair"
    )
    monkeypatch.setattr("muxplex.main.load_manifest", dict)

    captured_cmds: list[str] = []

    def mock_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    monkeypatch.setattr("muxplex.main.subprocess.run", mock_run)

    # Without force: the typed client surfaces the 409 as ApiError.
    with pytest.raises(ApiError) as exc_info:
        sync_client.delete_session(name)
    assert exc_info.value.status == 409
    assert captured_cmds == []  # nothing ran

    # With force=True: reaches the server, substitutes the default pair.
    sync_client.delete_session(name, force=True)
    assert len(captured_cmds) == 1

    # And the raw query param itself, independent of the typed client.
    raw = raw_http.delete(f"/api/sessions/{name}", params={"force": "true"})
    assert raw.status_code == 200
    assert raw.json().get("forced") is True


def test_session_commands_fields_present(sync_client, raw_http):
    """Same shape as test_sessions_fields_present: client model vs raw
    JSON, so a server rename turns this red in the same PR."""
    commands = sync_client.list_session_commands()
    raw = raw_http.get("/api/session-commands").json()

    assert len(commands.commands) == len(raw["commands"])
    parsed, raw_item = commands.commands[0], raw["commands"][0]
    assert parsed.id == raw_item["id"]
    assert parsed.label == raw_item["label"]
    assert parsed.new_session_template == raw_item["new_session_template"]
    assert parsed.delete_session_template == raw_item["delete_session_template"]
    assert commands.default_id == raw["default_id"]
    assert list(commands.errors) == raw["errors"]


@pytest.fixture
def followups_ready(monkeypatch, seeded_session):
    """Arm the bell hook and enable input for *seeded_session* -- the
    fences append_followup()/edit_followups() must pass to do anything
    real (same pattern as test_followups.py's `_enable`/`client` fixtures).
    """
    import copy

    from muxplex.settings import DEFAULT_SETTINGS

    monkeypatch.setattr("muxplex.main._bell_hook_armed", True)

    def _settings() -> dict:
        s = copy.deepcopy(DEFAULT_SETTINGS)
        s["input_enabled"] = True
        s["input_allowed_sessions"] = [seeded_session]
        return s

    monkeypatch.setattr("muxplex.main.load_settings", _settings)
    return seeded_session


def test_followups_round_trip(sync_client, followups_ready):
    """append -> read -> replace with the observed revision -> resume ->
    clear, against the real app."""
    name = followups_ready

    empty = sync_client.followups(name)
    assert empty.items == ()
    assert empty.revision == 0

    item = sync_client.append_followup(name, "run the tests", enter=True)
    assert item.text == "run the tests"
    assert item.enter is True

    queue = sync_client.followups(name)
    assert queue.revision == 1
    assert len(queue.items) == 1
    assert queue.items[0].id == item.id

    replaced = sync_client.replace_followups(
        name,
        [FollowupItem(id=item.id, text="run the tests --verbose", enter=True)],
        expected_revision=queue.revision,
    )
    assert replaced.revision == 2
    assert replaced.items[0].text == "run the tests --verbose"

    resumed = sync_client.resume_followups(name)
    assert resumed.halted is None

    cleared = sync_client.clear_followups(name)
    assert cleared is None
    final = sync_client.followups(name)
    assert final.items == ()


def test_followups_badge_parses_from_sessions(sync_client, followups_ready):
    """A queued item is visible via client.sessions()[0].followups.pending
    without a second round trip."""
    name = followups_ready
    sync_client.append_followup(name, "queued item")

    sessions = sync_client.sessions()
    assert len(sessions) == 1
    assert sessions[0].followups.pending == 1
    assert sessions[0].followups.halted is False


def test_edit_followups_retries_on_revision_conflict(sync_client, followups_ready):
    """The helper re-reads rather than retrying the same body -- assert
    the SECOND PUT carries the SECOND revision."""
    name = followups_ready
    sync_client.append_followup(name, "first item")

    put_revisions: list[int] = []
    orig_replace = sync_client.replace_followups

    call_count = 0

    def flaky_replace(session_name, items, *, expected_revision):
        nonlocal call_count
        call_count += 1
        put_revisions.append(expected_revision)
        if call_count == 1:
            # Simulate a concurrent mutation landing first: bump the
            # revision out from under this call so the real server 409s.
            sync_client.append_followup(session_name, "concurrent item")
        return orig_replace(session_name, items, expected_revision=expected_revision)

    sync_client.replace_followups = flaky_replace  # type: ignore[method-assign]
    try:
        result = sync_client.edit_followups(
            name, lambda items: list(items) + [], attempts=3
        )
    finally:
        del sync_client.replace_followups

    assert len(put_revisions) == 2
    assert put_revisions[1] == put_revisions[0] + 1
    assert result.revision >= 2


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
# bell.source -- must never be read by needs_attention (contract invariant,
# docs/plans/2026-08-07-bell-causality-plan.md §3/§9 test 3). Re-run the
# ENTIRE truth table above with every closed-enum source value injected;
# every result must be identical to the source-less baseline.
# ---------------------------------------------------------------------------

_BELL_SOURCE_VALUES = [None, "hook", "poll", "seeded", "halt", "some-future-value"]


@pytest.mark.parametrize(
    "unseen_count,seen_at,last_fired_at", _NEEDS_ATTENTION_TRUTH_TABLE
)
@pytest.mark.parametrize("source", _BELL_SOURCE_VALUES)
def test_needs_attention_ignores_source_for_every_enum_value(
    source, unseen_count, seen_at, last_fired_at
):
    server_bell = {
        "unseen_count": unseen_count,
        "seen_at": seen_at,
        "last_fired_at": last_fired_at,
        "source": source,
    }
    client_bell = Bell(
        unseen_count=unseen_count,
        seen_at=seen_at,
        last_fired_at=last_fired_at,
        source=source,
    )

    server_result = server_needs_attention(server_bell)
    client_result = client_bell.needs_attention
    baseline = server_needs_attention(
        {
            "unseen_count": unseen_count,
            "seen_at": seen_at,
            "last_fired_at": last_fired_at,
        }
    )

    assert server_result == baseline, (
        f"server needs_attention() changed when source={source!r} was present "
        f"-- it must NEVER read bell.source"
    )
    assert client_result == baseline, (
        f"Bell.needs_attention changed when source={source!r} was present "
        f"-- it must NEVER read bell.source"
    )


def test_bell_source_defaults_to_none_for_pre_feature_construction():
    """Every pre-existing Bell(...) construction site (this file's own
    truth-table test above, and any external caller) must keep compiling
    without passing source -- defaulted and last, per §4.5."""
    bell = Bell(unseen_count=0, seen_at=None, last_fired_at=None)
    assert bell.source is None


@pytest.mark.parametrize("raw_source", [None, "hook", "poll", "seeded", "halt"])
def test_parse_bell_round_trips_source(raw_source):
    """muxplex_client._protocol.parse_bell must parse every closed-enum
    value, and a bell dict that omits the key entirely (pre-feature
    server) must parse to source=None rather than raising."""
    from muxplex_client._protocol import parse_bell

    raw = {"last_fired_at": None, "seen_at": None, "unseen_count": 0}
    if raw_source is not None:
        raw["source"] = raw_source
    bell = parse_bell(raw)
    assert bell.source == raw_source


def test_parse_bell_tolerates_missing_source_key():
    """A bell dict from a pre-feature server has no "source" key at all --
    parse_bell must not raise, and must default to None."""
    from muxplex_client._protocol import parse_bell

    raw = {"last_fired_at": None, "seen_at": None, "unseen_count": 0}
    assert "source" not in raw
    bell = parse_bell(raw)
    assert bell.source is None


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


def test_rename_not_found_maps_to_session_not_found_error(sync_client, no_sessions):
    with pytest.raises(SessionNotFound):
        sync_client.rename_session("ghost", "ghost2")


def test_rename_fields_present(sync_client, raw_http, monkeypatch):
    """POST /api/sessions/{name}/rename -- field parity between the
    vendored client's RenameResult and the server's real response, driven
    through the real app (no tmux/rename mocking of the endpoint itself,
    only the tmux-touching helpers underneath it -- same pattern
    test_api.py uses for create/delete without a real tmux binary)."""
    import muxplex.main as main_mod

    monkeypatch.setattr(main_mod, "get_session_list", lambda: ["old-name"])

    async def _fake_rename(old, new):
        return None

    async def _fake_enumerate():
        return ["new-name"]

    monkeypatch.setattr(main_mod, "rename_tmux_session", _fake_rename)
    monkeypatch.setattr(main_mod, "enumerate_sessions", _fake_enumerate)

    result = sync_client.rename_session("old-name", "new-name")
    raw = raw_http.post(
        "/api/sessions/old-name/rename", json={"new_name": "new-name"}
    ).json()

    # First call already consumed the rename; re-seed for the raw comparison
    # call above to observe the SAME shape independently (idempotent
    # migrate_session_name makes this safe to call twice).
    assert result.from_name == "old-name"
    assert result.name == raw["name"] == "new-name"
    assert result.renamed is True
    assert result.migrated == raw["migrated"]


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

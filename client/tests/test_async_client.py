"""HTTP-shape tests for `async_client.AsyncMuxplexClient` -- no real server.

Mirrors test_sync_client.py exactly, `await`-shaped -- see that file's
module docstring for the full rationale (MockTransport over mocking
`_request()`, and why the byte-identical regression suite is load-bearing).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from muxplex_client.async_client import AsyncMuxplexClient
from muxplex_client.errors import (
    ApiError,
    RemoteError,
    RemoteNotFoundError,
    RemoteUnreachableError,
    TargetGoneError,
    TargetNotSelfOwningError,
)
from muxplex_client.models import FederationSessions, HeartbeatResult, Session

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _client(handler) -> AsyncMuxplexClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://test-server",
        transport=transport,
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )
    return AsyncMuxplexClient("https://test-server", client=http_client)


def _json_response(status_code: int, body: Any) -> httpx.Response:
    return httpx.Response(status_code, json=body)


# ---------------------------------------------------------------------------
# heartbeat()
# ---------------------------------------------------------------------------


async def test_heartbeat_sends_required_fields_only_when_optionals_omitted() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = __import__("json").loads(request.content)
        return _json_response(
            200, {"device_id": "d1", "status": "ok", "sync_group": "global"}
        )

    client = _client(handler)
    result = await client.heartbeat(device_id="d1", label="my-deck")

    assert captured["method"] == "POST"
    assert captured["path"] == "/api/heartbeat"
    assert captured["body"] == {
        "device_id": "d1",
        "label": "my-deck",
        "viewing_session": None,
        "view_mode": "grid",
        "last_interaction_at": 0.0,
    }
    assert "sync_group" not in captured["body"]
    assert "kind" not in captured["body"]
    assert result == HeartbeatResult(device_id="d1", status="ok", sync_group="global")


async def test_heartbeat_round_trips_every_new_parameter() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = __import__("json").loads(request.content)
        return _json_response(
            200,
            {"device_id": "d-deck-1", "status": "ok", "sync_group": "device:d-deck-1"},
        )

    client = _client(handler)
    result = await client.heartbeat(
        device_id="d-deck-1",
        label="Stream Deck (alienware)",
        viewing_session="work",
        view_mode="grid",
        last_interaction_at=123.5,
        sync_group="device:d-deck-1",
        kind="deck",
    )

    assert captured["body"] == {
        "device_id": "d-deck-1",
        "label": "Stream Deck (alienware)",
        "viewing_session": "work",
        "view_mode": "grid",
        "last_interaction_at": 123.5,
        "sync_group": "device:d-deck-1",
        "kind": "deck",
    }
    assert result == HeartbeatResult(
        device_id="d-deck-1", status="ok", sync_group="device:d-deck-1"
    )


async def test_heartbeat_target_gone_409_with_discriminator_raises() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            409, {"detail": {"target_gone": True, "device_id": "d-abc"}}
        )

    client = _client(handler)
    with pytest.raises(TargetGoneError) as exc_info:
        await client.heartbeat(
            device_id="d-deck", label="deck", sync_group="device:d-abc"
        )
    assert exc_info.value.status == 409


async def test_heartbeat_target_not_self_owning_400_with_discriminator_raises() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            400,
            {"detail": {"target_not_self_owning": True, "controlled_by": "d-f"}},
        )

    client = _client(handler)
    with pytest.raises(TargetNotSelfOwningError) as exc_info:
        await client.heartbeat(
            device_id="d-deck", label="deck", sync_group="device:d-x"
        )
    assert exc_info.value.status == 400


async def test_heartbeat_409_without_discriminator_is_plain_api_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(409, {"detail": "some other conflict"})

    client = _client(handler)
    with pytest.raises(ApiError) as exc_info:
        await client.heartbeat(device_id="d-deck", label="deck")
    assert exc_info.value.status == 409
    assert not isinstance(exc_info.value, TargetGoneError)


async def test_heartbeat_400_without_discriminator_is_plain_api_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(400, {"detail": "sync_group must be ..."})

    client = _client(handler)
    with pytest.raises(ApiError) as exc_info:
        await client.heartbeat(
            device_id="d-deck", label="deck", sync_group="device:other"
        )
    assert exc_info.value.status == 400
    assert not isinstance(exc_info.value, TargetNotSelfOwningError)


# ---------------------------------------------------------------------------
# state() / view() / connect() / set_active_view() -- device_id passthrough
# ---------------------------------------------------------------------------


async def test_state_with_device_id_sends_query_param() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["query"] = dict(request.url.params)
        return _json_response(
            200, {"active_session": "x", "active_view": "all", "sync_group": "global"}
        )

    client = _client(handler)
    await client.state(device_id="d-1")
    assert captured["query"] == {"device_id": "d-1"}


async def test_view_with_device_id_and_sort_sends_both_query_params() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["query"] = dict(request.url.params)
        return _json_response(200, {"view": "all", "views": [], "sort": "server"})

    client = _client(handler)
    await client.view(sort="alphabetical", device_id="d-1")
    assert captured["query"] == {"sort": "alphabetical", "device_id": "d-1"}


async def test_connect_with_device_id_sends_query_param() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["query"] = dict(request.url.params)
        captured["path"] = request.url.path
        return _json_response(200, {"active_session": "a", "ttyd_port": 7682})

    client = _client(handler)
    await client.connect("a", device_id="d-1")
    assert captured["path"] == "/api/sessions/a/connect"
    assert captured["query"] == {"device_id": "d-1"}


async def test_set_active_view_with_device_id_sends_query_param_and_body() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["query"] = dict(request.url.params)
        captured["body"] = __import__("json").loads(request.content)
        return _json_response(200, {"active_view": "work"})

    client = _client(handler)
    await client.set_active_view("work", device_id="d-1")
    assert captured["query"] == {"device_id": "d-1"}
    assert captured["body"] == {"active_view": "work"}


# ---------------------------------------------------------------------------
# Regression armor: byte-identical when device_id/kind are omitted
# ---------------------------------------------------------------------------


class TestByteIdenticalWithoutDeviceId:
    async def test_state_omits_device_id_entirely(self) -> None:
        captured: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["query_string"] = request.url.query
            captured["path"] = request.url.path
            return _json_response(200, {"active_session": None, "active_view": "all"})

        client = _client(handler)
        await client.state()
        assert captured["path"] == "/api/state"
        assert captured["query_string"] == b""

    async def test_view_no_args_omits_query_entirely(self) -> None:
        captured: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["query_string"] = request.url.query
            return _json_response(200, {"view": "all", "views": [], "sort": "server"})

        client = _client(handler)
        await client.view()
        assert captured["query_string"] == b""

    async def test_view_sort_only_matches_pre_pairing_query_shape(self) -> None:
        captured: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["query_string"] = request.url.query
            return _json_response(200, {"view": "all", "views": [], "sort": "server"})

        client = _client(handler)
        await client.view(sort="attention")
        assert captured["query_string"] == b"sort=attention"

    async def test_connect_omits_device_id_query_entirely(self) -> None:
        captured: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["query_string"] = request.url.query
            captured["path"] = request.url.path
            return _json_response(200, {"active_session": "a", "ttyd_port": 7682})

        client = _client(handler)
        await client.connect("a")
        assert captured["path"] == "/api/sessions/a/connect"
        assert captured["query_string"] == b""

    async def test_set_active_view_omits_device_id_query_entirely(self) -> None:
        captured: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["query_string"] = request.url.query
            captured["body"] = __import__("json").loads(request.content)
            return _json_response(200, {"active_view": "work"})

        client = _client(handler)
        await client.set_active_view("work")
        assert captured["query_string"] == b""
        assert captured["body"] == {"active_view": "work"}


# ---------------------------------------------------------------------------
# federation_sessions()
# ---------------------------------------------------------------------------


async def test_federation_sessions_sends_correct_path() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return _json_response(200, [])

    client = _client(handler)
    await client.federation_sessions()
    assert captured["path"] == "/api/federation/sessions"


async def test_federation_sessions_parses_mixed_local_remote_and_status() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            200,
            [
                {
                    "name": "local-work",
                    "snapshot": "",
                    "bell": {},
                    "deviceId": "d-local",
                    "deviceName": "my-machine",
                    "deviceVersion": "0.53.0",
                    "remoteId": None,
                    "sessionKey": "d-local:local-work",
                },
                {
                    "name": "dev",
                    "snapshot": "",
                    "bell": {},
                    "deviceId": "0",
                    "deviceName": "spark-2",
                    "deviceVersion": "0.52.0",
                    "remoteId": "0",
                    "sessionKey": "0:dev",
                },
                {
                    "status": "unreachable",
                    "deviceId": "1",
                    "remoteId": "1",
                    "deviceName": "alienware-r13",
                    "deviceVersion": None,
                },
            ],
        )

    client = _client(handler)
    result = await client.federation_sessions()

    assert isinstance(result, FederationSessions)
    assert len(result.sessions) == 2
    assert len(result.statuses) == 1
    local = next(s for s in result.sessions if s.name == "local-work")
    assert local.remote_id is None
    remote = next(s for s in result.sessions if s.name == "dev")
    assert remote.remote_id == "0"
    assert result.statuses[0].status == "unreachable"


async def test_federation_sessions_empty_remote_instances() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(200, [])

    client = _client(handler)
    result = await client.federation_sessions()
    assert result == FederationSessions(sessions=(), statuses=())


# ---------------------------------------------------------------------------
# connect(remote_id=...) -- federation connect-proxy routing
# ---------------------------------------------------------------------------


async def test_connect_with_remote_id_routes_to_federation_proxy_url() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["query_string"] = request.url.query
        return _json_response(200, {"active_session": "work", "ttyd_port": 7682})

    client = _client(handler)
    result = await client.connect("work", remote_id="0")

    assert captured["method"] == "POST"
    assert captured["path"] == "/api/federation/0/connect/work"
    assert captured["query_string"] == b""
    assert result.active_session == "work"
    assert result.ttyd_port == 7682


async def test_connect_without_remote_id_still_hits_local_endpoint() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return _json_response(200, {"active_session": "a", "ttyd_port": 7682})

    client = _client(handler)
    await client.connect("a")
    assert captured["path"] == "/api/sessions/a/connect"


async def test_connect_with_both_device_id_and_remote_id_raises_value_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not send a request when both are given")

    client = _client(handler)
    with pytest.raises(ValueError, match="mutually exclusive"):
        await client.connect("work", device_id="d-1", remote_id="0")


async def test_connect_remote_404_raises_remote_not_found_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Remote instance '9' not found"})

    client = _client(handler)
    with pytest.raises(RemoteNotFoundError) as exc_info:
        await client.connect("work", remote_id="9")
    assert exc_info.value.status == 404
    assert exc_info.value.device_id == "9"


async def test_connect_remote_502_raises_remote_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"detail": "Remote returned 500"})

    client = _client(handler)
    with pytest.raises(RemoteError) as exc_info:
        await client.connect("work", remote_id="0")
    assert exc_info.value.status == 502


async def test_connect_remote_503_raises_remote_unreachable_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503, json={"detail": "Remote unreachable: http://spark-2:8088"}
        )

    client = _client(handler)
    with pytest.raises(RemoteUnreachableError) as exc_info:
        await client.connect("work", remote_id="0")
    assert exc_info.value.status == 503


async def test_session_from_federation_sessions_can_be_passed_straight_to_connect() -> (
    None
):
    captured: dict[str, Any] = {}

    async def sessions_handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            200,
            [
                {
                    "name": "dev",
                    "snapshot": "",
                    "bell": {},
                    "deviceId": "0",
                    "deviceName": "spark-2",
                    "deviceVersion": "0.52.0",
                    "remoteId": "0",
                    "sessionKey": "0:dev",
                }
            ],
        )

    client = _client(sessions_handler)
    result = await client.federation_sessions()
    remote_session: Session = result.sessions[0]

    async def connect_handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return _json_response(200, {"active_session": "dev", "ttyd_port": 7683})

    client2 = _client(connect_handler)
    await client2.connect(remote_session.name, remote_id=remote_session.remote_id)
    assert captured["path"] == "/api/federation/0/connect/dev"

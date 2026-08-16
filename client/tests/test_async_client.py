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
    TargetGoneError,
    TargetNotSelfOwningError,
)
from muxplex_client.models import HeartbeatResult

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

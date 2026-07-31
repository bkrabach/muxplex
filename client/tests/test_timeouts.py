"""Pure tests for the SUBPROCESS_TIMEOUT per-request HTTP timeout override.

Covers the false-failure bug fix: create_session/delete_session/connect must
send a per-request HTTP timeout long enough for the server's operator-
supplied subprocess (new_session_template / delete_session_template / ttyd
restart) to finish, without changing the client-level default every other
(fast) endpoint keeps using. No network -- a duck-typed recording stand-in
for httpx.Client/httpx.AsyncClient captures exactly what `.request()` was
called with, the same style as test_cli.py's FakeClient. `cast()` gives the
stand-ins the constructor's declared type, same as test_cli.py does for its
own duck-typed FakeClient.
"""

from __future__ import annotations

from typing import Any, cast

import httpx
import pytest
from muxplex_client.async_client import AsyncMuxplexClient
from muxplex_client.constants import SUBPROCESS_TIMEOUT
from muxplex_client.errors import SessionWaitTimeout
from muxplex_client.sync_client import MuxplexClient

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal stand-in for httpx.Response -- only what _send/_request use."""

    def __init__(
        self, *, status_code: int = 200, json_body: Any = None, text: str = ""
    ) -> None:
        self.status_code = status_code
        self._json_body: Any = {} if json_body is None else json_body
        self.text = text

    def json(self) -> Any:
        return self._json_body


class _RecordingSyncClient:
    """Duck-typed stand-in for httpx.Client -- records every request() call."""

    def __init__(self, response: _FakeResponse | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._response = response or _FakeResponse()

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        timeout: Any = None,
    ) -> _FakeResponse:
        self.calls.append(
            {
                "method": method,
                "path": path,
                "params": params,
                "json": json,
                "timeout": timeout,
            }
        )
        return self._response


class _RecordingAsyncClient:
    """Async counterpart of _RecordingSyncClient."""

    def __init__(self, response: _FakeResponse | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._response = response or _FakeResponse()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        timeout: Any = None,
    ) -> _FakeResponse:
        self.calls.append(
            {
                "method": method,
                "path": path,
                "params": params,
                "json": json,
                "timeout": timeout,
            }
        )
        return self._response


def _sync(recording: _RecordingSyncClient) -> MuxplexClient:
    """Build a MuxplexClient wired to *recording* -- the injection seam the
    library's design reserves for this (`client=...`), cast to the
    constructor's declared type same as test_cli.py's FakeClient."""
    return MuxplexClient("http://testserver", client=cast(httpx.Client, recording))


def _async(recording: _RecordingAsyncClient) -> AsyncMuxplexClient:
    """Async counterpart of `_sync()`."""
    return AsyncMuxplexClient(
        "http://testserver", client=cast(httpx.AsyncClient, recording)
    )


# ---------------------------------------------------------------------------
# _send/_request/_request_text: omitted timeout -> httpx.USE_CLIENT_DEFAULT,
# explicit timeout -> passed straight through
# ---------------------------------------------------------------------------


def test_send_omits_explicit_timeout_by_default() -> None:
    recording = _RecordingSyncClient()
    client = _sync(recording)
    client._send("GET", "/health")
    assert recording.calls[0]["timeout"] is httpx.USE_CLIENT_DEFAULT


def test_send_passes_explicit_timeout_override() -> None:
    recording = _RecordingSyncClient()
    client = _sync(recording)
    client._send("POST", "/api/sessions", timeout=42.0)
    assert recording.calls[0]["timeout"] == 42.0


def test_request_and_request_text_propagate_timeout() -> None:
    recording = _RecordingSyncClient(_FakeResponse(json_body={"a": 1}, text="hi"))
    client = _sync(recording)

    client._request("GET", "/x", timeout=12.0)
    client._request_text("GET", "/y", timeout=13.0)

    assert recording.calls[0]["timeout"] == 12.0
    assert recording.calls[1]["timeout"] == 13.0


async def test_async_send_omits_explicit_timeout_by_default() -> None:
    recording = _RecordingAsyncClient()
    client = _async(recording)
    await client._send("GET", "/health")
    assert recording.calls[0]["timeout"] is httpx.USE_CLIENT_DEFAULT


async def test_async_send_passes_explicit_timeout_override() -> None:
    recording = _RecordingAsyncClient()
    client = _async(recording)
    await client._send("POST", "/api/sessions", timeout=42.0)
    assert recording.calls[0]["timeout"] == 42.0


async def test_async_request_and_request_text_propagate_timeout() -> None:
    recording = _RecordingAsyncClient(_FakeResponse(json_body={"a": 1}, text="hi"))
    client = _async(recording)

    await client._request("GET", "/x", timeout=12.0)
    await client._request_text("GET", "/y", timeout=13.0)

    assert recording.calls[0]["timeout"] == 12.0
    assert recording.calls[1]["timeout"] == 13.0


# ---------------------------------------------------------------------------
# create_session / delete_session / connect default to SUBPROCESS_TIMEOUT,
# and it is overridable via request_timeout
# ---------------------------------------------------------------------------


def test_create_session_uses_subprocess_timeout_by_default() -> None:
    recording = _RecordingSyncClient()
    client = _sync(recording)
    client.create_session("web", wait=False)
    assert recording.calls[0]["method"] == "POST"
    assert recording.calls[0]["path"] == "/api/sessions"
    assert recording.calls[0]["timeout"] == SUBPROCESS_TIMEOUT


def test_create_session_request_timeout_is_overridable() -> None:
    recording = _RecordingSyncClient()
    client = _sync(recording)
    client.create_session("web", wait=False, request_timeout=90.0)
    assert recording.calls[0]["timeout"] == 90.0


def test_delete_session_uses_subprocess_timeout_by_default() -> None:
    recording = _RecordingSyncClient()
    client = _sync(recording)
    client.delete_session("web")
    assert recording.calls[0]["method"] == "DELETE"
    assert recording.calls[0]["path"] == "/api/sessions/web"
    assert recording.calls[0]["timeout"] == SUBPROCESS_TIMEOUT


def test_delete_session_request_timeout_is_overridable() -> None:
    recording = _RecordingSyncClient()
    client = _sync(recording)
    client.delete_session("web", request_timeout=90.0)
    assert recording.calls[0]["timeout"] == 90.0


def test_connect_uses_subprocess_timeout_by_default() -> None:
    recording = _RecordingSyncClient(
        _FakeResponse(json_body={"active_session": "web", "ttyd_port": 7682})
    )
    client = _sync(recording)
    client.connect("web")
    assert recording.calls[0]["method"] == "POST"
    assert recording.calls[0]["path"] == "/api/sessions/web/connect"
    assert recording.calls[0]["timeout"] == SUBPROCESS_TIMEOUT


def test_connect_request_timeout_is_overridable() -> None:
    recording = _RecordingSyncClient(
        _FakeResponse(json_body={"active_session": "web", "ttyd_port": 7682})
    )
    client = _sync(recording)
    client.connect("web", request_timeout=90.0)
    assert recording.calls[0]["timeout"] == 90.0


def test_create_session_wait_timeout_is_independent_of_request_timeout() -> None:
    """The poll ceiling (`timeout=`) and the HTTP override (`request_timeout=`)
    must not collide -- passing both keeps each affecting only its own thing.

    The fake transport never reports the session as visible, so the poll
    ceiling legitimately expires and raises SessionWaitTimeout -- that's
    fine here; the point of this test is only what timeout each of the two
    calls (the POST and the polling GETs) was made with.
    """
    recording = _RecordingSyncClient()
    client = _sync(recording)
    with pytest.raises(SessionWaitTimeout):
        client.create_session(
            "web", wait=True, timeout=0.05, interval=0.01, request_timeout=90.0
        )
    assert recording.calls[0]["timeout"] == 90.0
    # The GET /api/sessions poll call(s) that follow use the client default,
    # never the request_timeout meant only for the POST.
    poll_calls = [c for c in recording.calls if c["method"] == "GET"]
    assert poll_calls
    for call in poll_calls:
        assert call["timeout"] is httpx.USE_CLIENT_DEFAULT


async def test_async_create_session_uses_subprocess_timeout_by_default() -> None:
    recording = _RecordingAsyncClient()
    client = _async(recording)
    await client.create_session("web", wait=False)
    assert recording.calls[0]["timeout"] == SUBPROCESS_TIMEOUT


async def test_async_delete_session_uses_subprocess_timeout_by_default() -> None:
    recording = _RecordingAsyncClient()
    client = _async(recording)
    await client.delete_session("web")
    assert recording.calls[0]["timeout"] == SUBPROCESS_TIMEOUT


async def test_async_connect_uses_subprocess_timeout_by_default() -> None:
    recording = _RecordingAsyncClient(
        _FakeResponse(json_body={"active_session": "web", "ttyd_port": 7682})
    )
    client = _async(recording)
    await client.connect("web")
    assert recording.calls[0]["timeout"] == SUBPROCESS_TIMEOUT


# ---------------------------------------------------------------------------
# Ordinary reads are unaffected -- proves the fix is scoped to only the
# three subprocess-backed endpoints, not a global timeout bump
# ---------------------------------------------------------------------------


def test_sessions_read_does_not_use_subprocess_timeout() -> None:
    recording = _RecordingSyncClient(_FakeResponse(json_body=[]))
    client = _sync(recording)
    client.sessions()
    assert recording.calls[0]["timeout"] is httpx.USE_CLIENT_DEFAULT


def test_delete_current_session_does_not_use_subprocess_timeout() -> None:
    """delete_current_session (DELETE /api/sessions/current) is a distinct
    endpoint from delete_session -- it disconnects ttyd, not a session
    subprocess, and must keep the ordinary client default."""
    recording = _RecordingSyncClient()
    client = _sync(recording)
    client.delete_current_session()
    assert recording.calls[0]["timeout"] is httpx.USE_CLIENT_DEFAULT


async def test_async_sessions_read_does_not_use_subprocess_timeout() -> None:
    recording = _RecordingAsyncClient(_FakeResponse(json_body=[]))
    client = _async(recording)
    await client.sessions()
    assert recording.calls[0]["timeout"] is httpx.USE_CLIENT_DEFAULT

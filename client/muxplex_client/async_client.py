"""Asynchronous muxplex API client (httpx.AsyncClient transport).

Signature-identical to `sync_client.MuxplexClient` with `await` and an
`Async` prefix. Required for Amplifier tool modules: `async def mount(...)`
tool execution is awaited, and a synchronous httpx call inside an async tool
would block the event loop for its duration -- for `run_shell_command`
polling a ten-minute build, that blocks the provider stream, every other
tool, and every hook. See _protocol.py's docstring for the shared logic this
wraps.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Callable, Self, Sequence

import httpx

from . import _protocol as protocol
from .constants import MIN_SERVER_VERSION
from .errors import ApiError, CommandTimeout, MuxplexError, UnreachableError
from .models import (
    CommandResult,
    ConnectResult,
    FocusResult,
    FollowupItem,
    FollowupQueue,
    InputResult,
    InstanceInfo,
    RenameResult,
    ServerState,
    Session,
    SessionCommands,
    SessionSnapshot,
    Settings,
    ViewResult,
)
from .sentinel import make_sentinel
from .sync_client import _extract_detail


class AsyncMuxplexClient:
    """Thin, typed, asynchronous HTTP client for the muxplex API.

    See `sync_client.MuxplexClient` for the full rationale behind every
    behavior here (Accept header, follow_redirects, ca_file, federation_key)
    -- this class mirrors it exactly, `await`-shaped.
    """

    def __init__(
        self,
        server_url: str,
        federation_key: str | None = None,
        *,
        ca_file: Path | str | None = None,
        timeout: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if client is not None:
            self._client = client
            self._owns_client = False
            return
        headers = {"Accept": "application/json"}
        if federation_key:
            headers["Authorization"] = f"Bearer {federation_key}"
        verify: bool | str = str(ca_file) if ca_file else True
        self._client = httpx.AsyncClient(
            base_url=server_url,
            headers=headers,
            verify=verify,
            timeout=timeout,
            follow_redirects=False,
        )
        self._owns_client = True

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        session_name: str | None = None,
    ) -> Any:
        try:
            response = await self._client.request(
                method, path, params=params, json=json
            )
        except httpx.HTTPError as exc:
            raise UnreachableError(f"{method} {path} failed: {exc}") from exc
        if response.status_code >= 400:
            raise protocol.map_status_error(
                response.status_code,
                path,
                _extract_detail(response),
                session_name=session_name,
            )
        return response.json()

    # ---- read ----

    async def instance_info(self) -> InstanceInfo:
        return protocol.parse_instance_info(
            await self._request("GET", "/api/instance-info")
        )

    async def sessions(self) -> list[Session]:
        return protocol.parse_sessions(await self._request("GET", "/api/sessions"))

    async def session(self, name: str, *, lines: int | None = None) -> SessionSnapshot:
        params = {"lines": lines} if lines is not None else None
        return protocol.parse_session_snapshot(
            await self._request(
                "GET", f"/api/sessions/{name}", params=params, session_name=name
            )
        )

    async def view(self, *, sort: str | None = None) -> ViewResult:
        params = {"sort": sort} if sort is not None else None
        return protocol.parse_view_result(
            await self._request("GET", "/api/view", params=params)
        )

    async def state(self) -> ServerState:
        return protocol.parse_server_state(await self._request("GET", "/api/state"))

    async def settings(self) -> Settings:
        return protocol.parse_settings(await self._request("GET", "/api/settings"))

    # ---- lifecycle ----

    async def create_session(
        self,
        name: str,
        *,
        command_id: str | None = None,
        wait: bool = True,
        timeout: float = 6.0,
        interval: float = 0.3,
    ) -> None:
        """See `sync_client.MuxplexClient.create_session` for the full
        `command_id` rationale -- identical here, `await`-shaped."""
        body: dict[str, Any] = {"name": name}
        if command_id is not None:
            body["command_id"] = command_id
        await self._request("POST", "/api/sessions", json=body, session_name=name)
        if wait and not await self.wait_for_session(
            name, timeout=timeout, interval=interval
        ):
            raise TimeoutError(
                f"session {name!r} did not appear in the read cache within {timeout}s"
            )

    async def delete_session(self, name: str, *, force: bool = False) -> None:
        """See `sync_client.MuxplexClient.delete_session` for the full
        `force` rationale -- identical here, `await`-shaped."""
        params = {"force": "true"} if force else None
        await self._request(
            "DELETE", f"/api/sessions/{name}", params=params, session_name=name
        )

    async def rename_session(self, name: str, new_name: str) -> RenameResult:
        """See `sync_client.MuxplexClient.rename_session` for the full
        rationale -- identical here, `await`-shaped."""
        body = {"new_name": new_name}
        return protocol.parse_rename_result(
            await self._request(
                "POST", f"/api/sessions/{name}/rename", json=body, session_name=name
            )
        )

    async def list_session_commands(self) -> SessionCommands:
        """GET /api/session-commands -- see
        `sync_client.MuxplexClient.list_session_commands`."""
        return protocol.parse_session_commands(
            await self._request("GET", "/api/session-commands")
        )

    async def wait_for_session(
        self, name: str, *, timeout: float = 6.0, interval: float = 0.3
    ) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            sessions = await self.sessions()
            if any(s.name == name for s in sessions):
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(interval)

    async def connect(self, name: str) -> ConnectResult:
        return protocol.parse_connect_result(
            await self._request(
                "POST", f"/api/sessions/{name}/connect", session_name=name
            )
        )

    async def set_active_view(self, view: str) -> None:
        await self._request("PATCH", "/api/state", json={"active_view": view})

    # ---- input ----

    async def send_input(
        self,
        name: str,
        *,
        text: str = "",
        keys: Sequence[str] = (),
        enter: bool = False,
        lines: int | None = None,
    ) -> InputResult:
        body: dict[str, Any] = {"text": text, "keys": list(keys), "enter": enter}
        if lines is not None:
            body["lines"] = lines
        return protocol.parse_input_result(
            await self._request(
                "POST", f"/api/sessions/{name}/input", json=body, session_name=name
            )
        )

    async def run_shell_command(
        self,
        name: str,
        command: str,
        *,
        timeout: float = 600.0,
        poll_interval: float = 2.0,
        lines: int = 500,
        bell_on_failure: bool = True,
        exit_expr: str = "$?",
        token: str | None = None,
    ) -> CommandResult:
        """Run *command* to completion and return its real exit code.

        See `sync_client.MuxplexClient.run_shell_command` for the full
        assumptions and rationale -- identical here, `await`-shaped. This is
        the operation that matters most for the async client: it never
        blocks the event loop while polling, unlike a synchronous call
        parked inside an async tool.
        """
        sentinel = make_sentinel(token)
        wrapped = sentinel.wrap(
            command, bell_on_failure=bell_on_failure, exit_expr=exit_expr
        )
        await self.send_input(name, text=wrapped, enter=True)

        start = time.monotonic()
        deadline = start + timeout
        while True:
            snap = await self.session(name, lines=lines)
            exit_code = sentinel.search(snap.snapshot)
            if exit_code is not None:
                return CommandResult(
                    session=name,
                    exit_code=exit_code,
                    snapshot=snap.snapshot,
                    elapsed=time.monotonic() - start,
                    token=sentinel.token,
                )
            if time.monotonic() >= deadline:
                raise CommandTimeout(
                    session=name,
                    token=sentinel.token,
                    elapsed=time.monotonic() - start,
                    snapshot=snap.snapshot,
                )
            await asyncio.sleep(poll_interval)

    # ---- follow-ups ----

    async def followups(self, name: str) -> FollowupQueue:
        """GET /api/sessions/{name}/followups."""
        return protocol.parse_followup_queue(
            await self._request(
                "GET", f"/api/sessions/{name}/followups", session_name=name
            )
        )

    async def append_followup(
        self, name: str, text: str, *, enter: bool = True
    ) -> FollowupItem:
        """POST /api/sessions/{name}/followups -- see
        `sync_client.MuxplexClient.append_followup` for the fence
        re-evaluation rationale."""
        body = {"text": text, "enter": enter}
        raw = await self._request(
            "POST", f"/api/sessions/{name}/followups", json=body, session_name=name
        )
        return protocol.parse_followup_item(raw["item"])

    async def replace_followups(
        self,
        name: str,
        items: Sequence[FollowupItem],
        *,
        expected_revision: int,
    ) -> FollowupQueue:
        """PUT /api/sessions/{name}/followups -- see
        `sync_client.MuxplexClient.replace_followups` for why
        `expected_revision` is required, never defaulted."""
        body: dict[str, Any] = {
            "expected_revision": expected_revision,
            "items": protocol.build_followup_items_body(items),
        }
        return protocol.parse_followup_queue(
            await self._request(
                "PUT", f"/api/sessions/{name}/followups", json=body, session_name=name
            )
        )

    async def clear_followups(self, name: str) -> None:
        """DELETE /api/sessions/{name}/followups -- clear items AND any halt."""
        await self._request(
            "DELETE", f"/api/sessions/{name}/followups", session_name=name
        )

    async def resume_followups(self, name: str) -> FollowupQueue:
        """POST /api/sessions/{name}/followups/resume -- clear the halt
        only, keeping every pending item and the current revision."""
        return protocol.parse_followup_queue(
            await self._request(
                "POST",
                f"/api/sessions/{name}/followups/resume",
                session_name=name,
            )
        )

    async def edit_followups(
        self,
        name: str,
        mutate: Callable[[tuple[FollowupItem, ...]], Sequence[FollowupItem]],
        *,
        attempts: int = 3,
    ) -> FollowupQueue:
        """GET -> mutate(items) -> PUT with the observed revision; retry on 409.

        See `sync_client.MuxplexClient.edit_followups` for the full
        rationale -- identical here, `await`-shaped. `mutate` itself
        stays a plain (non-async) callable in both clients: it is pure
        list transformation, no I/O.
        """
        last_error: ApiError | None = None
        for _ in range(attempts):
            current = await self.followups(name)
            new_items = mutate(current.items)
            try:
                return await self.replace_followups(
                    name, new_items, expected_revision=current.revision
                )
            except ApiError as exc:
                if exc.status != 409:
                    raise
                last_error = exc
        assert last_error is not None
        raise last_error

    # ---- focus ----

    async def raise_focus(self) -> FocusResult:
        """POST /api/focus -- see `sync_client.MuxplexClient.raise_focus` for the full contract."""
        return protocol.parse_focus_result(await self._request("POST", "/api/focus"))

    # ---- opt-in version check ----

    async def check_server(self, min_version: str = MIN_SERVER_VERSION) -> InstanceInfo:
        """Fetch instance-info and raise MuxplexError if the server is older.

        Never called automatically.
        """
        info = await self.instance_info()
        if protocol.version_tuple(info.version) < protocol.version_tuple(min_version):
            raise MuxplexError(
                f"server version {info.version!r} is older than required {min_version!r}"
            )
        return info

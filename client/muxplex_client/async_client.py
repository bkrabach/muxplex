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
from collections.abc import Callable, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any, Self

import httpx

from . import _protocol as protocol
from .config import ClientConfig, ca_remediation_hint, resolve_config
from .constants import MIN_SERVER_VERSION, SUBPROCESS_TIMEOUT
from .errors import (
    CommandTimeout,
    DestructiveChange,
    MuxplexError,
    SessionWaitTimeout,
    SettingsConflict,
    TlsTrustError,
    UnreachableError,
)
from .models import (
    CommandResult,
    ConnectResult,
    FederationEntry,
    InputResult,
    InstanceInfo,
    ServerState,
    Session,
    SessionSnapshot,
    Settings,
    ViewResult,
)
from .sentinel import make_sentinel
from .sync_client import _extract_error_body

# Re-exported here so `patch_state()`'s signature can spell its sentinel
# default the same way the spec does; see `_protocol._Unset`'s docstring.
UNSET = protocol.UNSET
_Unset = protocol._Unset


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
        self.config: ClientConfig | None = None
        self._ca_file: Path | None = Path(ca_file) if ca_file else None
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

    @classmethod
    def from_env(cls, **overrides: Any) -> Self:
        """Build a client from `resolve_config(**overrides)`.

        See `sync_client.MuxplexClient.from_env` -- identical behavior.
        """
        config = resolve_config(**overrides)
        client = cls(
            config.server_url,
            config.federation_key,
            ca_file=config.ca_file,
            timeout=config.timeout,
        )
        client.config = config
        return client

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        session_name: str | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """Perform one HTTP call and raise the mapped error for any failure.

        See `sync_client.MuxplexClient._send` -- identical behavior,
        `await`-shaped, including the *timeout* override and the
        `httpx.USE_CLIENT_DEFAULT` substitution for an omitted one. Shared
        by `_request` (JSON) and `_request_text` (PEM/plain-text); do not
        duplicate this block into either caller.
        """
        try:
            response = await self._client.request(
                method,
                path,
                params=params,
                json=json,
                timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
            )
        except httpx.HTTPError as exc:
            message = f"{method} {path} failed: {exc}"
            if "CERTIFICATE_VERIFY_FAILED" in str(exc):
                raise TlsTrustError(
                    message, hint=ca_remediation_hint(self._ca_file)
                ) from exc
            raise UnreachableError(message) from exc
        if response.status_code >= 400:
            detail, body = _extract_error_body(response)
            raise protocol.map_status_error(
                response.status_code,
                path,
                detail,
                session_name=session_name,
                body=body,
            )
        return response

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        session_name: str | None = None,
        timeout: float | None = None,
    ) -> Any:
        response = await self._send(
            method,
            path,
            params=params,
            json=json,
            session_name=session_name,
            timeout=timeout,
        )
        return response.json()

    async def _request_text(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        session_name: str | None = None,
        timeout: float | None = None,
    ) -> str:
        response = await self._send(
            method,
            path,
            params=params,
            json=json,
            session_name=session_name,
            timeout=timeout,
        )
        return response.text

    # ---- read ----

    async def health(self) -> dict[str, Any]:
        """GET /health -- unauthenticated liveness check."""
        return await self._request("GET", "/health")

    async def auth_mode(self) -> dict[str, Any]:
        """GET /auth/mode -- the server's auth mode and running username."""
        return await self._request("GET", "/auth/mode")

    async def ca_certificate(self) -> str:
        """GET /api/ca -- the local CA's public certificate PEM, unauthenticated.

        404s (surfaced as `ApiError`) when no local CA is configured -- see
        AGENTS.md / API_SEMANTICS.md's "GET /api/ca" section.
        """
        return await self._request_text("GET", "/api/ca")

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
        wait: bool = True,
        timeout: float = 6.0,
        interval: float = 0.3,
        request_timeout: float = SUBPROCESS_TIMEOUT,
    ) -> None:
        """Raises SessionWaitTimeout (also catchable as plain TimeoutError)
        if the session never appears -- see the sync client for the full
        rationale, including the *timeout* (poll ceiling) vs
        *request_timeout* (HTTP read timeout) distinction.
        """
        await self._request(
            "POST",
            "/api/sessions",
            json={"name": name},
            session_name=name,
            timeout=request_timeout,
        )
        if wait and not await self.wait_for_session(
            name, timeout=timeout, interval=interval
        ):
            raise SessionWaitTimeout(name, timeout)

    async def delete_session(
        self, name: str, *, request_timeout: float = SUBPROCESS_TIMEOUT
    ) -> None:
        """See `sync_client.MuxplexClient.delete_session` for the
        `request_timeout` rationale -- identical here, `await`-shaped.
        """
        await self._request(
            "DELETE",
            f"/api/sessions/{name}",
            session_name=name,
            timeout=request_timeout,
        )

    async def delete_current_session(self) -> None:
        """DELETE /api/sessions/current -- disconnect the current ttyd session."""
        await self._request("DELETE", "/api/sessions/current")

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

    async def connect(
        self, name: str, *, request_timeout: float = SUBPROCESS_TIMEOUT
    ) -> ConnectResult:
        """See `sync_client.MuxplexClient.connect` for the `request_timeout`
        rationale -- identical here, `await`-shaped.
        """
        return protocol.parse_connect_result(
            await self._request(
                "POST",
                f"/api/sessions/{name}/connect",
                session_name=name,
                timeout=request_timeout,
            )
        )

    async def patch_state(
        self,
        *,
        active_session: str | None | _Unset = UNSET,
        active_view: str | _Unset = UNSET,
        active_remote_id: str | None | _Unset = UNSET,
        session_order: Sequence[str] | _Unset = UNSET,
    ) -> None:
        """PATCH /api/state with only the fields explicitly passed.

        See `sync_client.MuxplexClient.patch_state` for the full rationale
        behind the `UNSET` sentinel.
        """
        body: dict[str, Any] = {}
        if not isinstance(active_session, _Unset):
            body["active_session"] = active_session
        if not isinstance(active_view, _Unset):
            body["active_view"] = active_view
        if not isinstance(active_remote_id, _Unset):
            body["active_remote_id"] = active_remote_id
        if not isinstance(session_order, _Unset):
            body["session_order"] = list(session_order)
        await self._request("PATCH", "/api/state", json=body)

    async def set_active_view(self, view: str) -> None:
        """PATCH /api/state {"active_view": view}.

        WARNING: active_view is server-global, last-writer-wins. Thin
        call-through to `patch_state()`.
        """
        await self.patch_state(active_view=view)

    # ---- settings ----

    async def update_settings(
        self,
        patch: dict[str, Any],
        *,
        expected_settings_updated_at: float | None = None,
        allow_destructive: bool = False,
    ) -> dict[str, Any]:
        """PATCH /api/settings.

        See `sync_client.MuxplexClient.update_settings` for the full
        rationale -- prefer `apply_settings()` for a caller-supplied
        mutation.
        """
        body = dict(patch)
        if expected_settings_updated_at is not None:
            body["expected_settings_updated_at"] = expected_settings_updated_at
        if allow_destructive:
            body["allow_destructive"] = True
        return await self._request("PATCH", "/api/settings", json=body)

    async def apply_settings(
        self,
        mutate: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        allow_destructive: bool = False,
    ) -> dict[str, Any]:
        """Read-modify-write PATCH /api/settings with automatic CAS retry.

        See `sync_client.MuxplexClient.apply_settings` for the full
        rationale, including the `input_enabled` / `input_allowed_sessions`
        `LOCAL_ONLY_KEYS` warning -- identical here, `await`-shaped.
        """

        async def _read() -> tuple[dict[str, Any], float | None]:
            current = await self._request("GET", "/api/settings")
            state = await self._request("GET", "/api/state")
            return current, state.get("settings_updated_at")

        current, expected_ts = await _read()
        patch = mutate(deepcopy(current))
        try:
            return await self.update_settings(
                patch,
                expected_settings_updated_at=expected_ts,
                allow_destructive=allow_destructive,
            )
        except DestructiveChange:
            raise
        except SettingsConflict:
            current, expected_ts = await _read()
            patch = mutate(deepcopy(current))
            return await self.update_settings(
                patch,
                expected_settings_updated_at=expected_ts,
                allow_destructive=allow_destructive,
            )

    async def settings_sync(self) -> dict[str, Any]:
        """GET /api/settings/sync -- syncable settings + timestamps."""
        return await self._request("GET", "/api/settings/sync")

    async def put_settings_sync(self, payload: dict[str, Any]) -> dict[str, Any]:
        """PUT /api/settings/sync -- push synced settings to a remote (newer-wins)."""
        return await self._request("PUT", "/api/settings/sync", json=payload)

    # ---- device / hooks ----

    async def heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /api/heartbeat -- register or update this device's heartbeat."""
        return await self._request("POST", "/api/heartbeat", json=payload)

    async def setup_hooks(self) -> dict[str, Any]:
        """POST /api/internal/setup-hooks -- re-register tmux hooks."""
        return await self._request("POST", "/api/internal/setup-hooks")

    # ---- bells ----

    async def ring_bell(self, name: str) -> None:
        """POST /api/sessions/{name}/bell -- record a bell fire for *name*."""
        await self._request("POST", f"/api/sessions/{name}/bell", session_name=name)

    async def clear_bell(self, name: str) -> None:
        """POST /api/sessions/{name}/bell/clear -- acknowledge *name*'s bell."""
        await self._request(
            "POST", f"/api/sessions/{name}/bell/clear", session_name=name
        )

    # ---- federation ----

    async def federation_sessions(self) -> list[FederationEntry]:
        """GET /api/federation/sessions -- local + remote sessions merged.

        See `sync_client.MuxplexClient.federation_sessions` for the
        in-band-failure rationale.
        """
        return protocol.parse_federation_entries(
            await self._request("GET", "/api/federation/sessions")
        )

    async def federation_connect(
        self, device_id: str, session_name: str
    ) -> dict[str, Any]:
        """POST /api/federation/{device_id}/connect/{session_name}."""
        return await self._request(
            "POST", f"/api/federation/{device_id}/connect/{session_name}"
        )

    async def federation_create_session(
        self, device_id: str, name: str
    ) -> dict[str, Any]:
        """POST /api/federation/{device_id}/sessions -- create *name* on a remote."""
        return await self._request(
            "POST", f"/api/federation/{device_id}/sessions", json={"name": name}
        )

    async def federation_delete_session(
        self, device_id: str, name: str
    ) -> dict[str, Any]:
        """DELETE /api/federation/{device_id}/sessions/{name} -- delete on a remote."""
        return await self._request(
            "DELETE", f"/api/federation/{device_id}/sessions/{name}"
        )

    async def federation_clear_bell(self, device_id: str, name: str) -> dict[str, Any]:
        """POST /api/federation/{device_id}/sessions/{name}/bell/clear."""
        return await self._request(
            "POST", f"/api/federation/{device_id}/sessions/{name}/bell/clear"
        )

    async def generate_federation_key(self) -> dict[str, Any]:
        """POST /api/federation/generate-key -- rotate this server's key."""
        return await self._request("POST", "/api/federation/generate-key")

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

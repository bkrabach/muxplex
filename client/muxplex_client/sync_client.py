"""Synchronous muxplex API client (httpx.Client transport).

Thin await-free shell over `_protocol.py` -- see that module's docstring.
Signature-identical to `async_client.AsyncMuxplexClient` with `await` and an
`Async` prefix.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Self, Sequence

import httpx

from . import _protocol as protocol
from .constants import MIN_SERVER_VERSION
from .errors import CommandTimeout, MuxplexError, UnreachableError
from .models import (
    CommandResult,
    ConnectResult,
    InputResult,
    InstanceInfo,
    ServerState,
    Session,
    SessionSnapshot,
    Settings,
    ViewResult,
)
from .sentinel import make_sentinel


class MuxplexClient:
    """Thin, typed, synchronous HTTP client for the muxplex API.

    Always sets `Accept: application/json` and `follow_redirects=False` in
    the constructor -- not left to callers. Without the header the auth
    middleware 307s an unauthenticated request to `/login`; a client that
    follows redirects would get a `200 OK` full of login-page HTML instead
    of the `401` it should see (AGENT_GUIDE.md §1).

    `federation_key=None` means "no credential" -- a localhost caller needs
    none (AGENT_GUIDE.md §1.1); an agent running on the same host as the
    server should not be forced to invent one.
    """

    def __init__(
        self,
        server_url: str,
        federation_key: str | None = None,
        *,
        ca_file: Path | str | None = None,
        timeout: float = 5.0,
        client: httpx.Client | None = None,
    ) -> None:
        if client is not None:
            self._client = client
            self._owns_client = False
            return
        headers = {"Accept": "application/json"}
        if federation_key:
            headers["Authorization"] = f"Bearer {federation_key}"
        # ca_file must be the CA, never the leaf -- a documented, expensive
        # footgun in this project (see AGENTS.md's "GET /api/ca" section).
        verify: bool | str = str(ca_file) if ca_file else True
        self._client = httpx.Client(
            base_url=server_url,
            headers=headers,
            verify=verify,
            timeout=timeout,
            follow_redirects=False,
        )
        self._owns_client = True

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        session_name: str | None = None,
    ) -> Any:
        try:
            response = self._client.request(method, path, params=params, json=json)
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

    def instance_info(self) -> InstanceInfo:
        """GET /api/instance-info -- public, no auth required."""
        return protocol.parse_instance_info(self._request("GET", "/api/instance-info"))

    def sessions(self) -> list[Session]:
        """GET /api/sessions -- the shared, ~2s-cycle poll cache."""
        return protocol.parse_sessions(self._request("GET", "/api/sessions"))

    def session(self, name: str, *, lines: int | None = None) -> SessionSnapshot:
        """GET /api/sessions/{name} -- one fresh capture-pane at a chosen depth."""
        params = {"lines": lines} if lines is not None else None
        return protocol.parse_session_snapshot(
            self._request(
                "GET", f"/api/sessions/{name}", params=params, session_name=name
            )
        )

    def view(self, *, sort: str | None = None) -> ViewResult:
        """GET /api/view -- the server-resolved current view."""
        params = {"sort": sort} if sort is not None else None
        return protocol.parse_view_result(
            self._request("GET", "/api/view", params=params)
        )

    def state(self) -> ServerState:
        """GET /api/state."""
        return protocol.parse_server_state(self._request("GET", "/api/state"))

    def settings(self) -> Settings:
        """GET /api/settings."""
        return protocol.parse_settings(self._request("GET", "/api/settings"))

    # ---- lifecycle ----

    def create_session(
        self,
        name: str,
        *,
        wait: bool = True,
        timeout: float = 6.0,
        interval: float = 0.3,
    ) -> None:
        """POST /api/sessions. With wait=True, polls until the session is
        visible in the ~2s read cache -- 0.3s interval, 6s ceiling, the
        measured schedule from AGENT_GUIDE.md §4. Raises TimeoutError if
        it never appears.
        """
        self._request("POST", "/api/sessions", json={"name": name}, session_name=name)
        if wait and not self.wait_for_session(name, timeout=timeout, interval=interval):
            raise TimeoutError(
                f"session {name!r} did not appear in the read cache within {timeout}s"
            )

    def delete_session(self, name: str) -> None:
        """DELETE /api/sessions/{name}."""
        self._request("DELETE", f"/api/sessions/{name}", session_name=name)

    def wait_for_session(
        self, name: str, *, timeout: float = 6.0, interval: float = 0.3
    ) -> bool:
        """Poll GET /api/sessions until *name* appears (poll-cache race)."""
        deadline = time.monotonic() + timeout
        while True:
            if any(s.name == name for s in self.sessions()):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(interval)

    def connect(self, name: str) -> ConnectResult:
        """POST /api/sessions/{name}/connect.

        WARNING: active_session is server-global. This moves the human's
        browser view too.
        """
        return protocol.parse_connect_result(
            self._request("POST", f"/api/sessions/{name}/connect", session_name=name)
        )

    def set_active_view(self, view: str) -> None:
        """PATCH /api/state {"active_view": view}.

        WARNING: active_view is server-global, last-writer-wins.
        """
        self._request("PATCH", "/api/state", json={"active_view": view})

    # ---- input ----

    def send_input(
        self,
        name: str,
        *,
        text: str = "",
        keys: Sequence[str] = (),
        enter: bool = False,
        lines: int | None = None,
    ) -> InputResult:
        """POST /api/sessions/{name}/input.

        At least one of text/keys/enter must be non-empty (else the server
        400s). Send order is text -> keys -> enter. Note that
        keys=["Enter"] with enter=True sends TWO Enters.
        """
        body: dict[str, Any] = {"text": text, "keys": list(keys), "enter": enter}
        if lines is not None:
            body["lines"] = lines
        return protocol.parse_input_result(
            self._request(
                "POST", f"/api/sessions/{name}/input", json=body, session_name=name
            )
        )

    def run_shell_command(
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

        ASSUMES the target pane is an idle POSIX shell prompt. If it is not
        (vim, a REPL, less, a TUI, an ssh session), this types a line into
        whatever is running and raises CommandTimeout.

        Composed entirely from send_input() + session() + Sentinel; rebuild
        it yourself from those if this shape does not fit.

        Raises CommandTimeout, InputForbidden, SessionNotFound.
        """
        sentinel = make_sentinel(token)
        wrapped = sentinel.wrap(
            command, bell_on_failure=bell_on_failure, exit_expr=exit_expr
        )
        self.send_input(name, text=wrapped, enter=True)

        start = time.monotonic()
        deadline = start + timeout
        while True:
            snap = self.session(name, lines=lines)
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
            time.sleep(poll_interval)

    # ---- opt-in version check ----

    def check_server(self, min_version: str = MIN_SERVER_VERSION) -> InstanceInfo:
        """Fetch instance-info and raise MuxplexError if the server is older.

        Never called automatically.
        """
        info = self.instance_info()
        if protocol.version_tuple(info.version) < protocol.version_tuple(min_version):
            raise MuxplexError(
                f"server version {info.version!r} is older than required {min_version!r}"
            )
        return info


def _extract_detail(response: httpx.Response) -> str:
    """Best-effort extraction of a FastAPI-style {"detail": ...} error body."""
    try:
        body = response.json()
        if isinstance(body, dict) and "detail" in body:
            return str(body["detail"])
    except Exception:
        pass
    return response.text

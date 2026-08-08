"""Synchronous muxplex API client (httpx.Client transport).

Thin await-free shell over `_protocol.py` -- see that module's docstring.
Signature-identical to `async_client.AsyncMuxplexClient` with `await` and an
`Async` prefix.
"""

from __future__ import annotations

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
    ServerState,
    Session,
    SessionCommands,
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
        command_id: str | None = None,
        wait: bool = True,
        timeout: float = 6.0,
        interval: float = 0.3,
    ) -> None:
        """POST /api/sessions. With wait=True, polls until the session is
        visible in the ~2s read cache -- 0.3s interval, 6s ceiling, the
        measured schedule from AGENT_GUIDE.md §4. Raises TimeoutError if
        it never appears.

        `command_id` selects a configured session command pair (see
        `list_session_commands()` / GET /api/session-commands;
        AGENT_GUIDE.md's `command_id` section) -- `None` (the default)
        omits the key entirely, byte-identical to a pre-feature request,
        and resolves server-side to the reserved "default" pair. An
        unresolvable id is a 400 (`ApiError` with `status == 400`); the
        `available` list in the detail is best discovered up front via
        `list_session_commands()` rather than parsed out of the error.
        """
        body: dict[str, Any] = {"name": name}
        if command_id is not None:
            body["command_id"] = command_id
        self._request("POST", "/api/sessions", json=body, session_name=name)
        if wait and not self.wait_for_session(name, timeout=timeout, interval=interval):
            raise TimeoutError(
                f"session {name!r} did not appear in the read cache within {timeout}s"
            )

    def delete_session(self, name: str, *, force: bool = False) -> None:
        """DELETE /api/sessions/{name}.

        When the session's recorded command pair no longer resolves (its
        `session_commands` entry was removed/edited), the server 409s and
        runs nothing. `force=True` sends `?force=true`, which substitutes
        the **default** kill command instead -- this may not perform the
        teardown the original pair would have (e.g. a custom `--destroy`
        cleanup step is skipped). Prefer restoring the pair in
        `settings.json` over forcing. `force=False` (the default) omits
        the query param entirely, byte-identical to a pre-feature request.
        """
        params = {"force": "true"} if force else None
        self._request(
            "DELETE", f"/api/sessions/{name}", params=params, session_name=name
        )

    def list_session_commands(self) -> SessionCommands:
        """GET /api/session-commands -- the canonical, server-resolved
        list of configured session command pairs. Clients must not
        re-derive this list from raw GET /api/settings
        (AGENT_GUIDE.md §"command_id").
        """
        return protocol.parse_session_commands(
            self._request("GET", "/api/session-commands")
        )

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

    # ---- follow-ups ----

    def followups(self, name: str) -> FollowupQueue:
        """GET /api/sessions/{name}/followups."""
        return protocol.parse_followup_queue(
            self._request("GET", f"/api/sessions/{name}/followups", session_name=name)
        )

    def append_followup(
        self, name: str, text: str, *, enter: bool = True
    ) -> FollowupItem:
        """POST /api/sessions/{name}/followups -- append one item.

        Enqueue-time fences (input_enabled / input_allowed_sessions / bell
        hook armed) are re-evaluated at fire time against fresh settings
        regardless of what was true here -- this call is a convenience
        check, not the safety boundary (AGENTS.md's "Follow-up queue"
        section).
        """
        body = {"text": text, "enter": enter}
        raw = self._request(
            "POST", f"/api/sessions/{name}/followups", json=body, session_name=name
        )
        return protocol.parse_followup_item(raw["item"])

    def replace_followups(
        self,
        name: str,
        items: Sequence[FollowupItem],
        *,
        expected_revision: int,
    ) -> FollowupQueue:
        """PUT /api/sessions/{name}/followups -- whole-list replace
        (edit + reorder + remove in one call).

        `expected_revision` is a REQUIRED precondition, never defaulted:
        the server 409s on mismatch rather than silently overwriting, and
        a default here would be the client silently choosing when
        re-executing already-typed text is acceptable. Prefer
        `edit_followups()` unless you already hold a fresh revision.
        """
        body: dict[str, Any] = {
            "expected_revision": expected_revision,
            "items": protocol.build_followup_items_body(items),
        }
        return protocol.parse_followup_queue(
            self._request(
                "PUT", f"/api/sessions/{name}/followups", json=body, session_name=name
            )
        )

    def clear_followups(self, name: str) -> None:
        """DELETE /api/sessions/{name}/followups -- clear items AND any halt."""
        self._request("DELETE", f"/api/sessions/{name}/followups", session_name=name)

    def resume_followups(self, name: str) -> FollowupQueue:
        """POST /api/sessions/{name}/followups/resume -- clear the halt
        only, keeping every pending item and the current revision.
        """
        return protocol.parse_followup_queue(
            self._request(
                "POST",
                f"/api/sessions/{name}/followups/resume",
                session_name=name,
            )
        )

    def edit_followups(
        self,
        name: str,
        mutate: Callable[[tuple[FollowupItem, ...]], Sequence[FollowupItem]],
        *,
        attempts: int = 3,
    ) -> FollowupQueue:
        """GET -> mutate(items) -> PUT with the observed revision; retry on 409.

        The revision-mismatch loop written once, correctly. `mutate`
        receives the current items and returns the new list. On a 409
        the queue is re-read and `mutate` re-applied to the FRESH items
        -- never the same body retried. Rebuild it yourself from
        `followups()`/`replace_followups()` if this shape does not fit
        (same judgment as `run_shell_command()`'s docstring).
        """
        last_error: ApiError | None = None
        for _ in range(attempts):
            current = self.followups(name)
            new_items = mutate(current.items)
            try:
                return self.replace_followups(
                    name, new_items, expected_revision=current.revision
                )
            except ApiError as exc:
                if exc.status != 409:
                    raise
                last_error = exc
        assert last_error is not None
        raise last_error

    # ---- focus ----

    def raise_focus(self) -> FocusResult:
        """POST /api/focus -- bring the SERVER's host muxplex PWA window to the foreground.

        No parameters: the endpoint accepts no target of any kind, always
        raising exactly the app the operator configured in that server's own
        ``settings.json`` (``focus_app``). See ../../docs/API_SEMANTICS.md's
        ``POST /api/focus`` section for the full contract, including the
        macOS-only ``open -a`` launch-if-not-running behavior.

        Raises ``ApiError`` (via ``map_status_error``) for every documented
        failure mode -- ``status`` distinguishes them: 501 (unsupported
        platform), 409 (``focus_app`` not configured on that host), 502
        (the mechanism ran and failed). Callers that want best-effort,
        never-raise semantics (e.g. firing focus alongside an unrelated
        action) should catch ``MuxplexError`` around this call.
        """
        return protocol.parse_focus_result(self._request("POST", "/api/focus"))

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

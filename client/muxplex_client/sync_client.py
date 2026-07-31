"""Synchronous muxplex API client (httpx.Client transport).

Thin await-free shell over `_protocol.py` -- see that module's docstring.
Signature-identical to `async_client.AsyncMuxplexClient` with `await` and an
`Async` prefix.
"""

from __future__ import annotations

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

# Re-exported here so `patch_state()`'s signature can spell its sentinel
# default the same way the spec does; see `_protocol._Unset`'s docstring.
UNSET = protocol.UNSET
_Unset = protocol._Unset


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
        self.config: ClientConfig | None = None
        self._ca_file: Path | None = Path(ca_file) if ca_file else None
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

    @classmethod
    def from_env(cls, **overrides: Any) -> Self:
        """Build a client from `resolve_config(**overrides)`.

        *overrides* accepts the same keyword arguments as
        `config.resolve_config` (`server_url`, `federation_key`, `key_file`,
        `ca_file`, `timeout`, `env`, `home`) -- explicit values here still
        win over the environment and disk, per that function's precedence
        rules. The resolved `ClientConfig` is kept on `.config` so a caller
        (a CLI's `info --verbose`, for instance) can inspect `.config.sources`
        to see where each value came from.
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

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _send(
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

        Shared by `_request` (JSON) and `_request_text` (PEM/plain-text) so
        the `UnreachableError`/`TlsTrustError` wrapping and
        `map_status_error` mapping exist in exactly one place; the two
        callers differ only in how they decode a *successful* response.
        Do not duplicate this block into either caller -- a divergence here
        is a silent correctness bug (see SPEC.md §1.3).

        *timeout*, when given, overrides this client's default read timeout
        for just this one request (httpx's per-request `timeout=`) -- used
        by the subprocess-backed endpoints documented on
        `constants.SUBPROCESS_TIMEOUT`. `None` (the default) leaves the
        client-level timeout from `__init__` untouched: passing `None`
        straight through to httpx would mean "no timeout at all", which is
        NOT what an omitted override should do, so the sentinel
        `httpx.USE_CLIENT_DEFAULT` is substituted instead. Do not confuse
        this HTTP-level knob with `create_session`'s own `timeout=`
        parameter, which is the unrelated poll-for-visibility ceiling
        waiting on the read cache.
        """
        try:
            response = self._client.request(
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

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        session_name: str | None = None,
        timeout: float | None = None,
    ) -> Any:
        return self._send(
            method,
            path,
            params=params,
            json=json,
            session_name=session_name,
            timeout=timeout,
        ).json()

    def _request_text(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        session_name: str | None = None,
        timeout: float | None = None,
    ) -> str:
        return self._send(
            method,
            path,
            params=params,
            json=json,
            session_name=session_name,
            timeout=timeout,
        ).text

    # ---- read ----

    def health(self) -> dict[str, Any]:
        """GET /health -- unauthenticated liveness check."""
        return self._request("GET", "/health")

    def auth_mode(self) -> dict[str, Any]:
        """GET /auth/mode -- the server's auth mode and running username."""
        return self._request("GET", "/auth/mode")

    def ca_certificate(self) -> str:
        """GET /api/ca -- the local CA's public certificate PEM, unauthenticated.

        404s (surfaced as `ApiError`) when no local CA is configured -- see
        AGENTS.md / API_SEMANTICS.md's "GET /api/ca" section.
        """
        return self._request_text("GET", "/api/ca")

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
        request_timeout: float = SUBPROCESS_TIMEOUT,
    ) -> None:
        """POST /api/sessions. With wait=True, polls until the session is
        visible in the ~2s read cache -- 0.3s interval, 6s ceiling, the
        measured schedule from AGENT_GUIDE.md §4. Raises SessionWaitTimeout
        (also catchable as plain TimeoutError) if it never appears.

        *timeout* is the poll-for-visibility ceiling above -- unrelated to
        HTTP. *request_timeout* is the HTTP read timeout for the POST
        itself: the server runs the operator's `new_session_template`
        synchronously before responding, which routinely takes longer than
        this client's ordinary (short) default read timeout -- see
        `constants.SUBPROCESS_TIMEOUT`. Raise `request_timeout` further if
        your template is slower than that.
        """
        self._request(
            "POST",
            "/api/sessions",
            json={"name": name},
            session_name=name,
            timeout=request_timeout,
        )
        if wait and not self.wait_for_session(name, timeout=timeout, interval=interval):
            raise SessionWaitTimeout(name, timeout)

    def delete_session(
        self, name: str, *, request_timeout: float = SUBPROCESS_TIMEOUT
    ) -> None:
        """DELETE /api/sessions/{name}.

        *request_timeout* is the HTTP read timeout override: the server
        runs the operator's `delete_session_template` synchronously (with
        `input="y\\n"`) before responding -- see `constants.SUBPROCESS_TIMEOUT`.
        """
        self._request(
            "DELETE",
            f"/api/sessions/{name}",
            session_name=name,
            timeout=request_timeout,
        )

    def delete_current_session(self) -> None:
        """DELETE /api/sessions/current -- disconnect the current ttyd session.

        Kills the running ttyd process and clears active_session in the
        server's persistent state.
        """
        self._request("DELETE", "/api/sessions/current")

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

    def connect(
        self, name: str, *, request_timeout: float = SUBPROCESS_TIMEOUT
    ) -> ConnectResult:
        """POST /api/sessions/{name}/connect.

        WARNING: active_session is server-global. This moves the human's
        browser view too.

        *request_timeout* is the HTTP read timeout override: the endpoint
        kills and restarts ttyd before responding, which is not
        instantaneous -- see `constants.SUBPROCESS_TIMEOUT`.
        """
        return protocol.parse_connect_result(
            self._request(
                "POST",
                f"/api/sessions/{name}/connect",
                session_name=name,
                timeout=request_timeout,
            )
        )

    def patch_state(
        self,
        *,
        active_session: str | None | _Unset = UNSET,
        active_view: str | _Unset = UNSET,
        active_remote_id: str | None | _Unset = UNSET,
        session_order: Sequence[str] | _Unset = UNSET,
    ) -> None:
        """PATCH /api/state with only the fields explicitly passed.

        Each keyword defaults to the `UNSET` sentinel, not `None` -- the
        server only touches `model_fields_set` (`main.py`'s `patch_state()`),
        so sending `null` for a field the caller never mentioned would
        *clear* it. `None` remains a perfectly valid value to pass
        explicitly (e.g. `active_session=None` clears the active session);
        only the *default* (omitted) is `UNSET`.

        WARNING: all four fields are server-global, last-writer-wins.
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
        self._request("PATCH", "/api/state", json=body)

    def set_active_view(self, view: str) -> None:
        """PATCH /api/state {"active_view": view}.

        WARNING: active_view is server-global, last-writer-wins. Thin
        call-through to `patch_state()`.
        """
        self.patch_state(active_view=view)

    # ---- settings ----

    def update_settings(
        self,
        patch: dict[str, Any],
        *,
        expected_settings_updated_at: float | None = None,
        allow_destructive: bool = False,
    ) -> dict[str, Any]:
        """PATCH /api/settings.

        Prefer `apply_settings()` for a caller-supplied mutation -- it
        handles the read, the CAS precondition, and the single-retry-on-
        conflict dance for you. Call this directly only when you already
        have both the patch and a `settings_updated_at` you trust.

        Raises `SettingsConflict` on a stale `expected_settings_updated_at`,
        or `DestructiveChange` (a `SettingsConflict` subclass -- check for
        it FIRST) when the patch would catastrophically shrink `views` and
        `allow_destructive` was not set. See errors.py for both.
        """
        body = dict(patch)
        if expected_settings_updated_at is not None:
            body["expected_settings_updated_at"] = expected_settings_updated_at
        if allow_destructive:
            body["allow_destructive"] = True
        return self._request("PATCH", "/api/settings", json=body)

    def apply_settings(
        self,
        mutate: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        allow_destructive: bool = False,
    ) -> dict[str, Any]:
        """Read-modify-write PATCH /api/settings with automatic CAS retry.

        1. `GET /api/settings` and `GET /api/state` for the current settings
           dict and `settings_updated_at`.
        2. `patch = mutate(deepcopy(current))` -- *mutate* returns the
           fields to PATCH (a subset of *current*, changed as desired).
        3. PATCH with `expected_settings_updated_at`.
        4. On `SettingsConflict` (CAS mismatch): re-read fresh settings and
           state, re-apply *mutate* to the FRESH data, retry exactly once,
           then propagate. Per API_SEMANTICS.md's `patchSettingsGuarded`
           reference behavior.
        5. On `DestructiveChange`: NEVER retried, propagated immediately.
           Only an explicit `allow_destructive=True` from the caller may
           pass the override through to the server.

        `input_enabled` and `input_allowed_sessions` are `LOCAL_ONLY_KEYS`
        (AGENT_GUIDE.md §7): the server silently ignores them here and still
        returns 200. A 200 from this method is never confirmation that
        input was enabled -- those two keys can only be changed by editing
        `settings.json` directly on the server.
        """

        def _read() -> tuple[dict[str, Any], float | None]:
            current = self._request("GET", "/api/settings")
            state = self._request("GET", "/api/state")
            return current, state.get("settings_updated_at")

        current, expected_ts = _read()
        patch = mutate(deepcopy(current))
        try:
            return self.update_settings(
                patch,
                expected_settings_updated_at=expected_ts,
                allow_destructive=allow_destructive,
            )
        except DestructiveChange:
            raise
        except SettingsConflict:
            current, expected_ts = _read()
            patch = mutate(deepcopy(current))
            return self.update_settings(
                patch,
                expected_settings_updated_at=expected_ts,
                allow_destructive=allow_destructive,
            )

    def settings_sync(self) -> dict[str, Any]:
        """GET /api/settings/sync -- syncable settings + timestamps.

        Federation-authenticated (Bearer token), same as any other
        non-exempt endpoint.
        """
        return self._request("GET", "/api/settings/sync")

    def put_settings_sync(self, payload: dict[str, Any]) -> dict[str, Any]:
        """PUT /api/settings/sync -- push synced settings to a remote (newer-wins).

        409s when *payload*'s `settings_updated_at` is not strictly newer
        than the remote's current value -- see API_SEMANTICS.md's
        "PUT /api/settings/sync" section.
        """
        return self._request("PUT", "/api/settings/sync", json=payload)

    # ---- device / hooks ----

    def heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /api/heartbeat -- register or update this device's heartbeat."""
        return self._request("POST", "/api/heartbeat", json=payload)

    def setup_hooks(self) -> dict[str, Any]:
        """POST /api/internal/setup-hooks -- re-register tmux hooks.

        Call after a tmux server restart.
        """
        return self._request("POST", "/api/internal/setup-hooks")

    # ---- bells ----

    def ring_bell(self, name: str) -> None:
        """POST /api/sessions/{name}/bell -- record a bell fire for *name*."""
        self._request("POST", f"/api/sessions/{name}/bell", session_name=name)

    def clear_bell(self, name: str) -> None:
        """POST /api/sessions/{name}/bell/clear -- acknowledge *name*'s bell."""
        self._request("POST", f"/api/sessions/{name}/bell/clear", session_name=name)

    # ---- federation ----

    def federation_sessions(self) -> list[FederationEntry]:
        """GET /api/federation/sessions -- local + remote sessions merged.

        A dead/unreachable/auth-failed remote never raises here -- it
        arrives in-band as a status entry with `name=None`; check
        `FederationEntry.is_session` before treating an entry as a real
        session. See API_SEMANTICS.md's federation section.
        """
        return protocol.parse_federation_entries(
            self._request("GET", "/api/federation/sessions")
        )

    def federation_connect(self, device_id: str, session_name: str) -> dict[str, Any]:
        """POST /api/federation/{device_id}/connect/{session_name}.

        Proxies a connect to the remote instance identified by *device_id*.
        """
        return self._request(
            "POST", f"/api/federation/{device_id}/connect/{session_name}"
        )

    def federation_create_session(self, device_id: str, name: str) -> dict[str, Any]:
        """POST /api/federation/{device_id}/sessions -- create *name* on a remote."""
        return self._request(
            "POST", f"/api/federation/{device_id}/sessions", json={"name": name}
        )

    def federation_delete_session(self, device_id: str, name: str) -> dict[str, Any]:
        """DELETE /api/federation/{device_id}/sessions/{name} -- delete on a remote."""
        return self._request("DELETE", f"/api/federation/{device_id}/sessions/{name}")

    def federation_clear_bell(self, device_id: str, name: str) -> dict[str, Any]:
        """POST /api/federation/{device_id}/sessions/{name}/bell/clear.

        Proxies a bell-clear to the remote instance identified by *device_id*.
        """
        return self._request(
            "POST", f"/api/federation/{device_id}/sessions/{name}/bell/clear"
        )

    def generate_federation_key(self) -> dict[str, Any]:
        """POST /api/federation/generate-key -- rotate this server's key.

        Overwrites the key file, invalidating every existing client
        authenticating with the old one. Returns `{"key": str, "path": str}`.
        """
        return self._request("POST", "/api/federation/generate-key")

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


def _extract_error_body(
    response: httpx.Response,
) -> tuple[str, dict[str, Any] | None]:
    """Best-effort extraction of a FastAPI-style error body.

    Returns the detail string (falling back to the raw response text when
    the body isn't JSON or has no "detail" key) alongside the full parsed
    body dict, or `None` when the body isn't a JSON object at all.
    `map_status_error` needs the full body too, for the settings 409's
    `backstop` / `settings_updated_at` / `counts` fields -- not just the
    detail string.
    """
    try:
        body = response.json()
    except ValueError:
        return response.text, None
    if isinstance(body, dict):
        return str(body.get("detail", response.text)), body
    return response.text, None

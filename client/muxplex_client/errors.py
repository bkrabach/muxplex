"""Exception hierarchy for the muxplex client.

Mapping from HTTP status to these types happens in `_protocol.map_status_error`
and is applied by both transport classes (`sync_client.MuxplexClient`,
`async_client.AsyncMuxplexClient`). See that function's docstring for the
exact rules.
"""

from __future__ import annotations


class MuxplexError(Exception):
    """Base class for all muxplex client errors."""


class UnreachableError(MuxplexError):
    """Transport failure -- connection refused, timeout, DNS, TLS.

    Raised when the request never got a response from the server at all
    (an `httpx.HTTPError` other than an HTTP status response).
    """


class AuthError(MuxplexError):
    """Credential rejected (401, or 403 from any endpoint except /input)."""


class SessionNotFound(MuxplexError):
    """404 for a session-scoped endpoint.

    Right after a `create_session()` call, this can be the ~2s read-model
    poll cache rather than a genuine failure -- see AGENT_GUIDE.md's "read
    model is eventually consistent" section, and `wait_for_session()` /
    `create_session(wait=True)`, which poll past this window.
    """

    def __init__(self, name: str, detail: str = "") -> None:
        self.name = name
        self.detail = detail
        super().__init__(detail or f"Session {name!r} not found")


class InputForbidden(MuxplexError):
    """403 from POST /api/sessions/{name}/input -- the operator's fence.

    Deliberately does NOT subclass `AuthError`: this is not a rejected
    credential, it means the session is not allowlisted for input (see
    `settings.input_enabled` / `settings.input_allowed_sessions` in the
    muxplex server). The correct caller response is to stop and tell the
    human what to add to `settings.json` -- a different action from
    rotating a key (AGENT_GUIDE.md §7).
    """

    def __init__(self, name: str, detail: str) -> None:
        self.name = name
        self.detail = detail
        super().__init__(detail)


class ApiError(MuxplexError):
    """Any other non-2xx response not covered by a more specific error."""

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"HTTP {status}: {detail}")


class CommandTimeout(MuxplexError):
    """`run_shell_command()` did not observe the completion sentinel in time."""

    def __init__(self, session: str, token: str, elapsed: float, snapshot: str) -> None:
        self.session = session
        self.token = token
        self.elapsed = elapsed
        self.snapshot = snapshot
        super().__init__(
            f"command in session {session!r} did not complete within "
            f"{elapsed:.1f}s (sentinel token={token!r})"
        )

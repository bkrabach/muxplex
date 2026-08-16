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


class TargetGoneError(ApiError):
    """409 from POST /api/heartbeat -- the device's claimed sync-group
    target no longer exists (its owning device was pruned).

    See docs/plans/2026-08-16-deck-control-target-design.md §7.1/§8.1 #5:
    the caller should fall back to `sync_group="global"` and re-send the
    heartbeat. Fixed at status 409 (unlike the generic `ApiError`, which
    takes any status) because this type IS the 409 case -- constructed
    with only `detail`.

    Not raised by any server today (the `target_gone` response shape is
    Step 2 of that design, not yet built) -- this type exists so the
    client recognizes it the moment a server starts sending it, with no
    follow-up client release required. Until then, `map_status_error()`
    never constructs this and a 409 heartbeat response maps to the
    generic `ApiError(409, ...)` exactly as it does today.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(409, detail)


class TargetNotSelfOwningError(ApiError):
    """400 from POST /api/heartbeat -- the requested target device is
    itself already following someone else (a follow-cycle attempt).

    See docs/plans/2026-08-16-deck-control-target-design.md §6.2.5/§7.0(b)/
    §8.1 #8. Fixed at status 400, constructed with only `detail`.

    Not raised by any server today (the `target_not_self_owning` response
    shape is Step 2 of that design, not yet built) -- see
    `TargetGoneError`'s docstring for the same forward-compatibility
    rationale. Until then, a 400 heartbeat response maps to the generic
    `ApiError(400, ...)` exactly as it does today.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(400, detail)


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

"""Exception hierarchy for the muxplex client.

Mapping from HTTP status to these types happens in `_protocol.map_status_error`
and is applied by both transport classes (`sync_client.MuxplexClient`,
`async_client.AsyncMuxplexClient`). See that function's docstring for the
exact rules.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class MuxplexError(Exception):
    """Base class for all muxplex client errors."""


class UnreachableError(MuxplexError):
    """Transport failure -- connection refused, timeout, DNS, TLS.

    Raised when the request never got a response from the server at all
    (an `httpx.HTTPError` other than an HTTP status response).
    """


class TlsTrustError(UnreachableError):
    """TLS certificate verification failed.

    A subclass of `UnreachableError` -- never got a real response from the
    server -- so existing `except UnreachableError` callers keep catching
    it unchanged. Raised specifically when the wrapped `httpx.HTTPError`
    stringifies to something containing `CERTIFICATE_VERIFY_FAILED`, the
    signature of the muxplex.crt-instead-of-CA footgun documented in
    AGENTS.md / API_SEMANTICS.md's "GET /api/ca" section. `.hint` carries
    `config.ca_remediation_hint()`'s ready-to-print remediation sentence,
    or `None` when nothing more useful can be said than the raw error.
    """

    def __init__(self, detail: str, *, hint: str | None = None) -> None:
        self.hint = hint
        super().__init__(detail)


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


class ConfigError(MuxplexError):
    """Bad or missing client configuration -- never a server condition.

    Raised by `config.resolve_config()` and its helpers (a malformed
    `MUXPLEX_TIMEOUT`, a missing/empty federation key file, ...). Distinct
    from every other error in this module: those all describe something
    the *server* said or failed to say; this describes something wrong
    with how the *client* itself was told to connect, before a single
    byte reaches the network.
    """


class SettingsConflict(MuxplexError):
    """409 from `PATCH /api/settings` -- the CAS precondition was rejected.

    Raised when `expected_settings_updated_at` no longer matches the
    server's current `settings_updated_at`: another writer (a browser
    tab, muxplex-deck, federation sync) updated settings concurrently.
    `.settings_updated_at` carries the server's current value from the
    response body, ready to retry against. See `MuxplexClient.apply_settings`
    for the safe read-modify-write built on top of this, and
    `docs/API_SEMANTICS.md` for the incident (7-of-8 views destroyed by a
    stale overwrite) this precondition closes.

    `DestructiveChange` is a SEPARATE 409 cause that subclasses this
    class syntactically but must NEVER be handled the same way -- see its
    docstring.
    """

    def __init__(
        self, detail: str, *, settings_updated_at: float | None = None
    ) -> None:
        self.settings_updated_at = settings_updated_at
        self.detail = detail
        super().__init__(detail)


class DestructiveChange(SettingsConflict):
    """409 from `PATCH /api/settings` with `backstop: true` in the body.

    A SEPARATE cause from the CAS mismatch above, even though the server
    reuses the same 409 status code: the write was rejected because it
    would catastrophically shrink `views` (see `docs/API_SEMANTICS.md`'s
    "Destructive-write backstop on views" section), not because the
    caller's timestamp was stale. `.counts` carries the before/after
    counts from the response body's `counts` field.

    **NEVER auto-retry this.** `SettingsConflict`'s ordinary CAS retry
    (re-read, re-apply, retry once) is safe because a stale write is
    simply out of date -- the fresh data fixes it. A destructive write is
    not out of date, it is *wrong*, and retrying it (even against fresh
    data) reproduces the same catastrophic intent. Only an explicit
    `allow_destructive=True` passed by the caller may override this, and
    federation sync is never allowed to. Because this subclasses
    `SettingsConflict`, an `except DestructiveChange` clause must be
    checked BEFORE a broader `except SettingsConflict` clause wherever
    both are handled -- see `MuxplexClient.apply_settings`.
    """

    def __init__(
        self,
        detail: str,
        *,
        settings_updated_at: float | None = None,
        counts: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(detail, settings_updated_at=settings_updated_at)
        self.counts: Mapping[str, Any] = counts or {}


class SessionWaitTimeout(MuxplexError, TimeoutError):
    """`create_session(wait=True)` gave up before the session appeared.

    Dual-inherits from both `MuxplexError` and builtins `TimeoutError` so
    every existing `except TimeoutError` caller keeps working unchanged
    while `except MuxplexError` -- what a CLI needs to catch every client
    error uniformly -- now also catches this. Replaces the bare
    `TimeoutError` previously raised directly in `create_session()`.
    """

    def __init__(self, name: str, timeout: float) -> None:
        self.name = name
        self.timeout = timeout
        super().__init__(
            f"session {name!r} did not appear in the read cache within {timeout}s"
        )

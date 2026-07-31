"""Pure protocol logic: response parsing and error mapping.

No I/O, no httpx import -- tested once in `tests/test_protocol.py` with no
network. `sync_client.py` and `async_client.py` are each a thin, ~120-line
await-shaped (or not) shell that calls into this module; duplication is
confined to that shell, where it is honest and cheap (this is what httpx
itself does for its own sync/async split).

`.get()`-based parsing throughout, deliberately: AGENTS.md requires clients
to tolerate unknown fields and the server to tolerate their absence. Missing
keys fall back to a sane default rather than raising KeyError.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping, Sequence
from typing import Any

from .constants import DEFAULT_CAPTURE_LINES
from .errors import (
    ApiError,
    AuthError,
    DestructiveChange,
    InputForbidden,
    MuxplexError,
    SessionNotFound,
    SettingsConflict,
)
from .models import (
    Bell,
    ConnectResult,
    FederationEntry,
    InputResult,
    InstanceInfo,
    ServerState,
    Session,
    SessionSnapshot,
    Settings,
    View,
    ViewResult,
    ViewSession,
)

__all__ = [
    "UNSET",
    "map_status_error",
    "parse_bell",
    "parse_connect_result",
    "parse_federation_entries",
    "parse_federation_entry",
    "parse_input_result",
    "parse_instance_info",
    "parse_server_state",
    "parse_session",
    "parse_session_snapshot",
    "parse_sessions",
    "parse_settings",
    "parse_view",
    "parse_view_result",
    "parse_view_session",
    "version_tuple",
]


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def parse_bell(raw: Mapping[str, Any]) -> Bell:
    return Bell(
        last_fired_at=raw.get("last_fired_at"),
        seen_at=raw.get("seen_at"),
        unseen_count=int(raw.get("unseen_count", 0)),
    )


def parse_session(raw: Mapping[str, Any]) -> Session:
    return Session(
        name=raw["name"],
        snapshot=raw.get("snapshot", ""),
        bell=parse_bell(raw.get("bell") or {}),
        last_activity_at=raw.get("last_activity_at"),
    )


def parse_sessions(raw: Sequence[Mapping[str, Any]]) -> list[Session]:
    return [parse_session(item) for item in raw]


def parse_session_snapshot(raw: Mapping[str, Any]) -> SessionSnapshot:
    return SessionSnapshot(
        name=raw["name"],
        snapshot=raw.get("snapshot", ""),
        lines=int(raw.get("lines", DEFAULT_CAPTURE_LINES)),
        bell=parse_bell(raw.get("bell") or {}),
        last_activity_at=raw.get("last_activity_at"),
    )


def parse_view_session(raw: Mapping[str, Any]) -> ViewSession:
    return ViewSession(
        name=raw["name"],
        active=bool(raw.get("active", False)),
        needs_attention=bool(raw.get("needs_attention", False)),
        bell=parse_bell(raw.get("bell") or {}),
        last_activity_at=raw.get("last_activity_at"),
    )


def parse_view_result(raw: Mapping[str, Any]) -> ViewResult:
    return ViewResult(
        view=raw.get("view", "all"),
        views=tuple(raw.get("views") or ()),
        sort=raw.get("sort", "server"),
        sessions=tuple(parse_view_session(s) for s in (raw.get("sessions") or ())),
    )


def parse_server_state(raw: Mapping[str, Any]) -> ServerState:
    return ServerState(
        active_session=raw.get("active_session"),
        active_view=raw.get("active_view") or "all",
        settings_updated_at=raw.get("settings_updated_at"),
        raw=raw,
    )


def parse_view(raw: Mapping[str, Any]) -> View:
    return View(
        name=raw.get("name", ""),
        sessions=frozenset(raw.get("sessions") or ()),
    )


def parse_settings(raw: Mapping[str, Any]) -> Settings:
    return Settings(
        views=tuple(parse_view(v) for v in (raw.get("views") or ())),
        hidden_sessions=frozenset(raw.get("hidden_sessions") or ()),
        sort_order=raw.get("sort_order", "manual"),
        raw=raw,
    )


def parse_instance_info(raw: Mapping[str, Any]) -> InstanceInfo:
    return InstanceInfo(
        name=raw.get("name", ""),
        device_id=raw.get("device_id", ""),
        version=raw.get("version", ""),
        federation_enabled=bool(raw.get("federation_enabled", False)),
        tmux_socket_dir=raw.get("tmux_socket_dir"),
        bell_hook_armed=raw.get("bell_hook_armed"),
        raw=raw,
    )


def parse_connect_result(raw: Mapping[str, Any]) -> ConnectResult:
    return ConnectResult(
        active_session=raw["active_session"],
        ttyd_port=int(raw["ttyd_port"]),
    )


def parse_input_result(raw: Mapping[str, Any]) -> InputResult:
    return InputResult(
        session=raw["session"],
        snapshot=raw.get("snapshot", ""),
    )


def parse_federation_entry(raw: Mapping[str, Any]) -> FederationEntry:
    return FederationEntry(
        device_id=raw.get("deviceId"),
        device_name=raw.get("deviceName"),
        device_version=raw.get("deviceVersion"),
        remote_id=raw.get("remoteId"),
        session_key=raw.get("sessionKey"),
        name=raw.get("name"),
        status=raw.get("status"),
        raw=raw,
    )


def parse_federation_entries(raw: Sequence[Mapping[str, Any]]) -> list[FederationEntry]:
    return [parse_federation_entry(item) for item in raw]


# ---------------------------------------------------------------------------
# UNSET sentinel -- backs patch_state()'s "field not passed" vs "field
# explicitly set to None" distinction
# ---------------------------------------------------------------------------


class _Unset(enum.Enum):
    """Sentinel type for an omitted `patch_state()` keyword argument.

    `None` is itself a meaningful, sendable value for `active_session` (it
    clears the field), so "this argument was not passed" cannot be spelled
    as `None` the way it usually is -- the server only touches
    `model_fields_set` (see `main.py`'s `patch_state()`), so sending `null`
    for a field the caller never mentioned would silently *clear* it. A
    distinct sentinel type, with exactly one member (`UNSET`, below), makes
    "omitted" and "explicitly cleared" different values that `is UNSET` (or
    `isinstance(x, _Unset)`) can tell apart. An `Enum` of one member is a
    singleton by construction -- no custom `__new__` needed to guarantee it.
    """

    UNSET = enum.auto()

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET = _Unset.UNSET


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def map_status_error(
    status: int,
    path: str,
    detail: str,
    *,
    session_name: str | None = None,
    body: Mapping[str, Any] | None = None,
) -> MuxplexError:
    """Map an HTTP error status + path to the matching MuxplexError subclass.

    Rules (applied in order):
      - 401 -> AuthError (credential rejected).
      - 403 from a path ending in "/input" -> InputForbidden (the operator's
        allowlist fence, NOT a bad credential).
      - 403 from any other path -> AuthError.
      - 404 from a session-scoped call (the caller passed *session_name*)
        -> SessionNotFound (session_name is whatever the caller was
        targeting; right after create_session() this can be the read-model
        poll cache rather than a real failure). A 404 from a call that did
        NOT pass *session_name* is NOT a missing tmux session -- e.g.
        `GET /api/ca` 404s when no local CA is configured, and the
        federation proxy endpoints 404 on an unknown `device_id` -- and
        must not be misreported as one. Every method that targets a
        session-scoped path (`/api/sessions/{name}` and its `/input`,
        `/bell`, `/bell/clear`, `/connect` children) passes `session_name=`
        for exactly this reason; see sync_client.py/async_client.py. The
        federation_* proxy methods deliberately do NOT pass it: their 404
        means "no remote matches this device_id", a different failure an
        agent must not confuse with a missing session by catching
        SessionNotFound and reacting as if a session vanished.
      - 404 from any other call -> ApiError(status, detail).
      - 409 from exactly "/api/settings" -> SettingsConflict (the
        `expected_settings_updated_at` CAS precondition failed), or
        DestructiveChange when *body*'s `backstop` field is `True` (the
        views-collapse guard). These are told apart ONLY by that field --
        see errors.DestructiveChange's docstring for why conflating the two
        is exactly how a real incident repeats itself. `body` is the full
        parsed JSON error body (not just `detail`); when unavailable this
        falls back to an ordinary SettingsConflict with no timestamp.
      - Anything else -> ApiError(status, detail).
    """
    if status == 401:
        return AuthError(detail)
    if status == 403:
        if path.endswith("/input"):
            return InputForbidden(session_name or "", detail)
        return AuthError(detail)
    if status == 404:
        if session_name is not None:
            return SessionNotFound(session_name, detail)
        return ApiError(status, detail)
    if status == 409 and path == "/api/settings":
        body = body or {}
        settings_updated_at = body.get("settings_updated_at")
        if body.get("backstop") is True:
            return DestructiveChange(
                detail,
                settings_updated_at=settings_updated_at,
                counts=body.get("counts") or {},
            )
        return SettingsConflict(detail, settings_updated_at=settings_updated_at)
    return ApiError(status, detail)


# ---------------------------------------------------------------------------
# Version comparison (backs the opt-in check_server() helper)
# ---------------------------------------------------------------------------


def version_tuple(version: str) -> tuple[int, ...]:
    """Parse a dotted version string into a comparable tuple of ints.

    Deliberately lenient: non-numeric suffixes (e.g. "1.2.3rc1") are reduced
    to their leading digits, and an entirely non-numeric segment becomes 0
    rather than raising -- this is a convenience comparison for an opt-in
    caller, not a strict semver parser.
    """
    parts: list[int] = []
    for piece in version.split("."):
        digits = ""
        for ch in piece:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts)

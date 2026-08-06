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

from typing import Any, Mapping, Sequence

from .constants import DEFAULT_CAPTURE_LINES
from .errors import ApiError, AuthError, InputForbidden, MuxplexError, SessionNotFound
from .models import (
    Bell,
    ConnectResult,
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
    "map_status_error",
    "parse_bell",
    "parse_session",
    "parse_sessions",
    "parse_session_snapshot",
    "parse_view_session",
    "parse_view_result",
    "parse_server_state",
    "parse_view",
    "parse_settings",
    "parse_instance_info",
    "parse_connect_result",
    "parse_input_result",
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
        # Tolerant of a pre-feature server that omits `views` entirely
        # (AUTO_VIEWS_SPEC.md §10.1) -- absence parses to an empty tuple,
        # never a KeyError.
        views=tuple(raw.get("views") or ()),
        created_at=raw.get("created_at"),
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
        server_started_at=raw.get("server_started_at"),
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


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def map_status_error(
    status: int,
    path: str,
    detail: str,
    *,
    session_name: str | None = None,
) -> MuxplexError:
    """Map an HTTP error status + path to the matching MuxplexError subclass.

    Rules (applied in order):
      - 401 -> AuthError (credential rejected).
      - 403 from a path ending in "/input" -> InputForbidden (the operator's
        allowlist fence, NOT a bad credential).
      - 403 from any other path -> AuthError.
      - 404 -> SessionNotFound (session_name is whatever the caller was
        targeting; right after create_session() this can be the read-model
        poll cache rather than a real failure).
      - Anything else -> ApiError(status, detail).
    """
    if status == 401:
        return AuthError(detail)
    if status == 403:
        if path.endswith("/input"):
            return InputForbidden(session_name or "", detail)
        return AuthError(detail)
    if status == 404:
        return SessionNotFound(session_name or "", detail)
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

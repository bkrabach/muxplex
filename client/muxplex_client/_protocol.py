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
    FocusResult,
    FollowupItem,
    FollowupQueue,
    Followups,
    InputResult,
    InstanceInfo,
    ServerState,
    Session,
    SessionCommand,
    SessionCommands,
    SessionSnapshot,
    Settings,
    View,
    ViewResult,
    ViewSession,
)

__all__ = [
    "map_status_error",
    "parse_bell",
    "parse_followups",
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
    "parse_focus_result",
    "parse_followup_item",
    "parse_followup_queue",
    "build_followup_items_body",
    "parse_session_command",
    "parse_session_commands",
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


def parse_followups(raw: Mapping[str, Any] | None) -> Followups:
    """Parse the `followups` badge sub-object on GET /api/sessions and
    GET /api/sessions/{name} entries. `None`/absent (a pre-feature server)
    parses to `Followups()` -- same tolerance pattern as `parse_bell`.
    """
    if not raw:
        return Followups()
    return Followups(
        pending=int(raw.get("pending", 0)),
        halted=bool(raw.get("halted", False)),
    )


def parse_session(raw: Mapping[str, Any]) -> Session:
    return Session(
        name=raw["name"],
        snapshot=raw.get("snapshot", ""),
        bell=parse_bell(raw.get("bell") or {}),
        last_activity_at=raw.get("last_activity_at"),
        # Tolerant of a pre-feature server that omits `views` entirely
        # (docs/plans/2026-08-04-auto-views-plan.md §10.1) -- absence parses to an empty tuple,
        # never a KeyError.
        views=tuple(raw.get("views") or ()),
        created_at=raw.get("created_at"),
        followups=parse_followups(raw.get("followups")),
        cwd=raw.get("cwd"),
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
        # Field-parity additions (docs/plans/2026-08-07-agent-surface-additive-plan.md
        # §6.4/§7.4) -- all four default so a pre-parity server parses cleanly.
        created_at=raw.get("created_at"),
        followups=parse_followups(raw.get("followups")),
        views=tuple(raw.get("views") or ()),
        cwd=raw.get("cwd"),
        # Scrollback-paging additions (docs/plans/2026-08-07-scrollback-paging-plan.md
        # §3.3/§5) -- default `None`/`False` so a pre-paging server parses
        # cleanly. `start`/`row_count`/`total` are left `None` (rather than
        # `0`) when absent, so a caller can distinguish "no data" from a
        # real empty page (`before=0` reports `row_count=0` explicitly).
        start=raw.get("start"),
        row_count=raw.get("row_count"),
        total=raw.get("total"),
        has_more=bool(raw.get("has_more", False)),
        saturated=bool(raw.get("saturated", False)),
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


def parse_focus_result(raw: Mapping[str, Any]) -> FocusResult:
    return FocusResult(
        platform=raw.get("platform", ""),
        app=raw.get("app", ""),
    )


def parse_followup_item(raw: Mapping[str, Any]) -> FollowupItem:
    return FollowupItem(
        id=raw["id"],
        text=raw.get("text", ""),
        enter=bool(raw.get("enter", True)),
        created_at=raw.get("created_at"),
    )


def parse_followup_queue(raw: Mapping[str, Any]) -> FollowupQueue:
    return FollowupQueue(
        session=raw["session"],
        revision=int(raw.get("revision", 0)),
        items=tuple(parse_followup_item(it) for it in (raw.get("items") or ())),
        halted=raw.get("halted"),
        target_window=raw.get("target_window"),
    )


def build_followup_items_body(items: Sequence[FollowupItem]) -> list[dict[str, Any]]:
    """Build the wire body for PUT .../followups' `items` list.

    An item's `id` is sent as `None` when falsy (e.g. the caller
    constructed a brand-new `FollowupItem` with `id=""`) rather than an
    empty string -- the server's `FollowupItemInput.id: str | None`
    treats absent/unknown id as "new" identically either way, but `None`
    is the honest wire shape for "no id yet".
    """
    return [{"id": it.id or None, "text": it.text, "enter": it.enter} for it in items]


def parse_session_command(raw: Mapping[str, Any]) -> SessionCommand:
    return SessionCommand(
        id=raw["id"],
        label=raw.get("label", ""),
        new_session_template=raw.get("new_session_template", ""),
        delete_session_template=raw.get("delete_session_template", ""),
    )


def parse_session_commands(raw: Mapping[str, Any]) -> SessionCommands:
    return SessionCommands(
        commands=tuple(parse_session_command(c) for c in (raw.get("commands") or ())),
        default_id=raw.get("default_id", "default"),
        errors=tuple(raw.get("errors") or ()),
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

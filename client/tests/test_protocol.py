"""Pure tests for muxplex_client._protocol -- no network, no server import.

Exercises response parsing from canned dicts (including missing-field
tolerance, per AGENTS.md's "clients tolerate unknown fields" contract) and
the complete error-mapping table.
"""

from __future__ import annotations

from muxplex_client import _protocol as protocol
from muxplex_client.errors import (
    ApiError,
    AuthError,
    DestructiveChange,
    InputForbidden,
    SessionNotFound,
    SettingsConflict,
)
from muxplex_client.models import Bell

# ---------------------------------------------------------------------------
# Bell / needs_attention
# ---------------------------------------------------------------------------


def test_parse_bell_full() -> None:
    bell = protocol.parse_bell(
        {"last_fired_at": 100.0, "seen_at": 50.0, "unseen_count": 2}
    )
    assert bell == Bell(last_fired_at=100.0, seen_at=50.0, unseen_count=2)


def test_parse_bell_missing_keys_default() -> None:
    bell = protocol.parse_bell({})
    assert bell == Bell(last_fired_at=None, seen_at=None, unseen_count=0)


def test_bell_needs_attention_unseen_zero() -> None:
    assert (
        Bell(last_fired_at=None, seen_at=None, unseen_count=0).needs_attention is False
    )


def test_bell_needs_attention_never_seen() -> None:
    assert Bell(last_fired_at=1.0, seen_at=None, unseen_count=1).needs_attention is True


def test_bell_needs_attention_fired_after_seen() -> None:
    assert Bell(last_fired_at=2.0, seen_at=1.0, unseen_count=1).needs_attention is True


def test_bell_needs_attention_fired_before_seen() -> None:
    assert Bell(last_fired_at=1.0, seen_at=2.0, unseen_count=1).needs_attention is False


def test_bell_needs_attention_fired_equals_seen() -> None:
    assert Bell(last_fired_at=1.0, seen_at=1.0, unseen_count=1).needs_attention is False


def test_bell_needs_attention_last_fired_none_seen_not_none() -> None:
    # Defensive edge case per the server's own docstring: should not occur in
    # practice (last_fired_at is always set alongside unseen_count), but must
    # not raise.
    assert (
        Bell(last_fired_at=None, seen_at=1.0, unseen_count=1).needs_attention is False
    )


# ---------------------------------------------------------------------------
# Session / SessionSnapshot
# ---------------------------------------------------------------------------


def test_parse_session_full() -> None:
    session = protocol.parse_session(
        {
            "name": "alpha",
            "snapshot": "pane text",
            "bell": {"last_fired_at": None, "seen_at": None, "unseen_count": 0},
            "last_activity_at": 123.0,
        }
    )
    assert session.name == "alpha"
    assert session.snapshot == "pane text"
    assert session.last_activity_at == 123.0


def test_parse_session_missing_last_activity_at_tolerated() -> None:
    """Servers predating the activity feature omit this field entirely."""
    session = protocol.parse_session({"name": "alpha", "snapshot": "", "bell": {}})
    assert session.last_activity_at is None


def test_parse_session_unknown_extra_fields_tolerated() -> None:
    """AGENTS.md: clients must tolerate unknown fields."""
    session = protocol.parse_session(
        {
            "name": "alpha",
            "snapshot": "",
            "bell": {},
            "some_future_field": "ignored",
        }
    )
    assert session.name == "alpha"


def test_parse_sessions_list() -> None:
    raw = [
        {"name": "a", "snapshot": "", "bell": {}},
        {"name": "b", "snapshot": "", "bell": {}},
    ]
    sessions = protocol.parse_sessions(raw)
    assert [s.name for s in sessions] == ["a", "b"]


def test_parse_session_snapshot_defaults_lines() -> None:
    snap = protocol.parse_session_snapshot({"name": "a", "snapshot": "x", "bell": {}})
    assert snap.lines == 30  # DEFAULT_CAPTURE_LINES


# ---------------------------------------------------------------------------
# ViewResult
# ---------------------------------------------------------------------------


def test_parse_view_result() -> None:
    raw = {
        "view": "all",
        "views": ["all", "work"],
        "sort": "server",
        "sessions": [
            {
                "name": "a",
                "active": True,
                "needs_attention": False,
                "bell": {},
                "last_activity_at": None,
            }
        ],
    }
    result = protocol.parse_view_result(raw)
    assert result.view == "all"
    assert result.views == ("all", "work")
    assert result.sort == "server"
    assert len(result.sessions) == 1
    assert result.sessions[0].active is True


def test_parse_view_result_empty_sessions() -> None:
    result = protocol.parse_view_result(
        {"view": "unknown-view", "views": [], "sort": "server"}
    )
    assert result.sessions == ()


# ---------------------------------------------------------------------------
# ServerState / Settings / InstanceInfo -- missing-field tolerance + raw
# ---------------------------------------------------------------------------


def test_parse_server_state_active_view_defaults_to_all() -> None:
    state = protocol.parse_server_state({"active_session": None, "active_view": ""})
    assert state.active_view == "all"


def test_parse_server_state_missing_settings_updated_at() -> None:
    state = protocol.parse_server_state({"active_session": "x", "active_view": "all"})
    assert state.settings_updated_at is None


def test_parse_server_state_preserves_raw() -> None:
    raw = {"active_session": "x", "active_view": "all", "future_field": 42}
    state = protocol.parse_server_state(raw)
    assert state.raw == raw


def test_parse_settings_defaults() -> None:
    settings = protocol.parse_settings({})
    assert settings.views == ()
    assert settings.hidden_sessions == frozenset()
    assert settings.sort_order == "manual"


def test_parse_settings_views_and_hidden() -> None:
    raw = {
        "views": [{"name": "work", "sessions": ["dev1:a", "dev1:b"]}],
        "hidden_sessions": ["dev1:c"],
        "sort_order": "alphabetical",
    }
    settings = protocol.parse_settings(raw)
    assert settings.views[0].name == "work"
    assert settings.views[0].sessions == frozenset({"dev1:a", "dev1:b"})
    assert settings.hidden_sessions == frozenset({"dev1:c"})
    assert settings.sort_order == "alphabetical"


def test_parse_instance_info_bell_hook_armed_absent_is_none() -> None:
    """None on servers < 0.18.0 that predate this field."""
    info = protocol.parse_instance_info(
        {"name": "x", "device_id": "y", "version": "0.17.0"}
    )
    assert info.bell_hook_armed is None


def test_parse_instance_info_preserves_raw() -> None:
    raw = {"name": "x", "device_id": "y", "version": "0.18.0", "bell_hook_armed": True}
    info = protocol.parse_instance_info(raw)
    assert info.raw == raw
    assert info.bell_hook_armed is True


# ---------------------------------------------------------------------------
# ConnectResult / InputResult
# ---------------------------------------------------------------------------


def test_parse_connect_result() -> None:
    result = protocol.parse_connect_result({"active_session": "a", "ttyd_port": 7682})
    assert result.active_session == "a"
    assert result.ttyd_port == 7682


def test_parse_input_result() -> None:
    result = protocol.parse_input_result(
        {"ok": True, "session": "a", "snapshot": "text"}
    )
    assert result.session == "a"
    assert result.snapshot == "text"


# ---------------------------------------------------------------------------
# Error mapping -- the complete table
# ---------------------------------------------------------------------------


def test_map_401_is_auth_error() -> None:
    err = protocol.map_status_error(401, "/api/sessions", "nope")
    assert isinstance(err, AuthError)


def test_map_403_on_input_path_is_input_forbidden() -> None:
    err = protocol.map_status_error(
        403, "/api/sessions/alpha/input", "not allowlisted", session_name="alpha"
    )
    assert isinstance(err, InputForbidden)
    assert not isinstance(err, AuthError)  # deliberately NOT a subclass
    assert err.name == "alpha"
    assert err.detail == "not allowlisted"


def test_map_403_on_non_input_path_is_auth_error() -> None:
    err = protocol.map_status_error(403, "/api/sessions", "forbidden")
    assert isinstance(err, AuthError)
    assert not isinstance(err, InputForbidden)


def test_map_404_is_session_not_found() -> None:
    err = protocol.map_status_error(
        404, "/api/sessions/ghost", "Session 'ghost' not found", session_name="ghost"
    )
    assert isinstance(err, SessionNotFound)
    assert err.name == "ghost"


def test_map_other_status_is_api_error() -> None:
    err = protocol.map_status_error(500, "/api/sessions", "boom")
    assert isinstance(err, ApiError)
    assert err.status == 500
    assert err.detail == "boom"


def test_map_400_is_api_error() -> None:
    err = protocol.map_status_error(400, "/api/sessions/x", "bad request")
    assert isinstance(err, ApiError)
    assert err.status == 400


def test_map_409_on_settings_with_no_backstop_is_settings_conflict() -> None:
    body = {
        "detail": "Settings have changed since you last loaded them.",
        "settings_updated_at": 123.5,
    }
    err = protocol.map_status_error(409, "/api/settings", body["detail"], body=body)
    assert isinstance(err, SettingsConflict)
    assert not isinstance(err, DestructiveChange)
    assert err.settings_updated_at == 123.5


def test_map_409_on_settings_with_backstop_true_is_destructive_change() -> None:
    body = {
        "detail": "views would collapse from 8 to 1",
        "settings_updated_at": 200.0,
        "backstop": True,
        "counts": {"before": 8, "after": 1},
    }
    err = protocol.map_status_error(409, "/api/settings", body["detail"], body=body)
    assert isinstance(err, DestructiveChange)
    assert isinstance(err, SettingsConflict)  # subclass, but told apart by backstop
    assert err.settings_updated_at == 200.0
    assert err.counts == {"before": 8, "after": 1}


def test_map_409_on_settings_without_body_falls_back_to_settings_conflict() -> None:
    """No body at all (e.g. a caller that didn't pass one) still yields a
    SettingsConflict, never crashes, and never a DestructiveChange."""
    err = protocol.map_status_error(409, "/api/settings", "conflict")
    assert isinstance(err, SettingsConflict)
    assert not isinstance(err, DestructiveChange)
    assert err.settings_updated_at is None


def test_map_409_on_other_path_is_api_error() -> None:
    """The settings-specific 409 mapping is scoped to exactly /api/settings --
    a 409 from any other path (e.g. /api/settings/sync) is an ordinary
    ApiError, not a SettingsConflict."""
    err = protocol.map_status_error(
        409, "/api/settings/sync", "stale", body={"backstop": True}
    )
    assert isinstance(err, ApiError)
    assert not isinstance(err, SettingsConflict)
    assert err.status == 409


# ---------------------------------------------------------------------------
# FederationEntry parsing
# ---------------------------------------------------------------------------


def test_parse_federation_entry_real_session() -> None:
    raw = {
        "name": "alpha",
        "snapshot": "pane text",
        "bell": {},
        "last_activity_at": None,
        "deviceId": "dev1",
        "deviceName": "laptop",
        "deviceVersion": "0.30.1",
        "remoteId": None,
        "sessionKey": "dev1:alpha",
    }
    entry = protocol.parse_federation_entry(raw)
    assert entry.is_session is True
    assert entry.name == "alpha"
    assert entry.status is None
    assert entry.device_id == "dev1"
    assert entry.device_name == "laptop"
    assert entry.device_version == "0.30.1"
    assert entry.remote_id is None
    assert entry.session_key == "dev1:alpha"
    assert entry.raw == raw


def test_parse_federation_entry_unreachable_status_has_no_name() -> None:
    raw = {
        "status": "unreachable",
        "deviceId": "dev2",
        "remoteId": "dev2",
        "deviceName": "phone",
        "deviceVersion": None,
    }
    entry = protocol.parse_federation_entry(raw)
    assert entry.is_session is False
    assert entry.name is None
    assert entry.status == "unreachable"
    assert entry.device_version is None  # UNKNOWN, never "same as ours"


def test_parse_federation_entry_auth_failed_status() -> None:
    raw = {
        "status": "auth_failed",
        "deviceId": "dev3",
        "remoteId": "dev3",
        "deviceName": "desktop",
        "deviceVersion": "0.29.0",
    }
    entry = protocol.parse_federation_entry(raw)
    assert entry.is_session is False
    assert entry.status == "auth_failed"
    assert entry.name is None


def test_parse_federation_entry_empty_status() -> None:
    raw = {
        "status": "empty",
        "deviceId": "dev4",
        "remoteId": "dev4",
        "deviceName": "server",
        "deviceVersion": "0.30.1",
    }
    entry = protocol.parse_federation_entry(raw)
    assert entry.is_session is False
    assert entry.status == "empty"


def test_parse_federation_entries_mixed_list() -> None:
    raw = [
        {
            "name": "a",
            "snapshot": "",
            "bell": {},
            "deviceId": "dev1",
            "deviceName": "laptop",
            "deviceVersion": "0.30.1",
            "remoteId": None,
            "sessionKey": "dev1:a",
        },
        {
            "status": "unreachable",
            "deviceId": "dev2",
            "remoteId": "dev2",
            "deviceName": "phone",
            "deviceVersion": None,
        },
    ]
    entries = protocol.parse_federation_entries(raw)
    assert len(entries) == 2
    assert entries[0].is_session is True
    assert entries[1].is_session is False


# ---------------------------------------------------------------------------
# UNSET sentinel
# ---------------------------------------------------------------------------


def test_unset_is_a_singleton() -> None:
    assert protocol._Unset.UNSET is protocol.UNSET
    assert len(protocol._Unset) == 1


def test_unset_is_falsy() -> None:
    assert bool(protocol.UNSET) is False


def test_unset_repr() -> None:
    assert repr(protocol.UNSET) == "UNSET"


# ---------------------------------------------------------------------------
# version_tuple
# ---------------------------------------------------------------------------


def test_version_tuple_basic() -> None:
    assert protocol.version_tuple("0.18.0") == (0, 18, 0)


def test_version_tuple_ordering() -> None:
    assert protocol.version_tuple("0.17.0") < protocol.version_tuple("0.18.0")
    assert protocol.version_tuple("0.18.1") > protocol.version_tuple("0.18.0")


def test_version_tuple_non_numeric_suffix() -> None:
    assert protocol.version_tuple("1.2.3rc1") == (1, 2, 3)

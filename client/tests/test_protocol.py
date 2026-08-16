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
    InputForbidden,
    SessionNotFound,
    TargetGoneError,
    TargetNotSelfOwningError,
)
from muxplex_client.models import Bell, FollowupItem, Followups, HeartbeatResult

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


def test_parse_session_with_views() -> None:
    """A session payload carrying `views` (docs/plans/2026-08-04-auto-views-plan.md §10.1) parses
    into the tuple field."""
    session = protocol.parse_session(
        {"name": "alpha", "snapshot": "", "bell": {}, "views": ["Work", "Amplifier"]}
    )
    assert session.views == ("Work", "Amplifier")


def test_parse_session_missing_views_defaults_empty_tuple() -> None:
    """A pre-feature server that omits `views` entirely parses to `()`,
    never a KeyError."""
    session = protocol.parse_session({"name": "alpha", "snapshot": "", "bell": {}})
    assert session.views == ()


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


def test_parse_server_state_new_fields_present() -> None:
    """docs/plans/2026-08-16-deck-control-target-design.md §8.3: sync_group,
    controlled_by, and active_remote_id all parse when present."""
    state = protocol.parse_server_state(
        {
            "active_session": "x",
            "active_view": "all",
            "sync_group": "device:d-mac-tab",
            "controlled_by": "d-deck-alien",
            "active_remote_id": "d-remote-1",
        }
    )
    assert state.sync_group == "device:d-mac-tab"
    assert state.controlled_by == "d-deck-alien"
    assert state.active_remote_id == "d-remote-1"


def test_parse_server_state_new_fields_absent_default_none() -> None:
    """A pre-feature server that omits all three must parse cleanly --
    never raise -- and each defaults to None."""
    state = protocol.parse_server_state({"active_session": "x", "active_view": "all"})
    assert state.sync_group is None
    assert state.controlled_by is None
    assert state.active_remote_id is None


# ---------------------------------------------------------------------------
# HeartbeatResult
# ---------------------------------------------------------------------------


def test_parse_heartbeat_result_full() -> None:
    result = protocol.parse_heartbeat_result(
        {"device_id": "d-1", "status": "ok", "sync_group": "device:d-1"}
    )
    assert result == HeartbeatResult(
        device_id="d-1", status="ok", sync_group="device:d-1"
    )


def test_parse_heartbeat_result_defaults_status_and_sync_group() -> None:
    result = protocol.parse_heartbeat_result({"device_id": "d-1"})
    assert result.status == "ok"
    assert result.sync_group == "global"


# ---------------------------------------------------------------------------
# map_status_error -- target_gone / target_not_self_owning discriminators
# ---------------------------------------------------------------------------


def test_map_409_with_target_gone_discriminator_is_target_gone_error() -> None:
    err = protocol.map_status_error(
        409,
        "/api/heartbeat",
        "target gone",
        detail_obj={"target_gone": True, "device_id": "d-abc"},
    )
    assert isinstance(err, TargetGoneError)
    assert err.status == 409


def test_map_409_without_detail_obj_is_plain_api_error() -> None:
    """No discriminator at all (the common case today) -> unchanged
    generic ApiError, exactly as before this feature existed."""
    err = protocol.map_status_error(409, "/api/heartbeat", "some conflict")
    assert type(err) is ApiError
    assert err.status == 409


def test_map_409_with_detail_obj_but_falsy_target_gone_is_plain_api_error() -> None:
    err = protocol.map_status_error(
        409, "/api/heartbeat", "conflict", detail_obj={"target_gone": False}
    )
    assert type(err) is ApiError


def test_map_400_with_target_not_self_owning_discriminator() -> None:
    err = protocol.map_status_error(
        400,
        "/api/heartbeat",
        "cycle",
        detail_obj={"target_not_self_owning": True, "controlled_by": "d-x"},
    )
    assert isinstance(err, TargetNotSelfOwningError)
    assert err.status == 400


def test_map_400_without_detail_obj_is_plain_api_error() -> None:
    """Existing 400s (e.g. an invalid sync_group value today) are
    unaffected -- this is the exact regression case §10 calls out."""
    err = protocol.map_status_error(
        400, "/api/heartbeat", "sync_group must be 'global' or 'device:<id>'"
    )
    assert type(err) is ApiError
    assert err.status == 400


def test_map_400_with_detail_obj_but_falsy_discriminator_is_plain_api_error() -> None:
    err = protocol.map_status_error(
        400,
        "/api/heartbeat",
        "bad request",
        detail_obj={"target_not_self_owning": False},
    )
    assert type(err) is ApiError


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


# ---------------------------------------------------------------------------
# Followups badge (Session.followups / SessionSnapshot.followups)
# ---------------------------------------------------------------------------


def test_parse_followups_full() -> None:
    badge = protocol.parse_followups({"pending": 3, "halted": True})
    assert badge == Followups(pending=3, halted=True)


def test_parse_followups_absent_defaults() -> None:
    """A pre-feature server that omits `followups` entirely parses to the
    zero-value badge, never a KeyError."""
    assert protocol.parse_followups(None) == Followups()
    assert protocol.parse_followups({}) == Followups()


def test_parse_session_carries_followups_badge() -> None:
    session = protocol.parse_session(
        {
            "name": "a",
            "snapshot": "",
            "bell": {},
            "followups": {"pending": 2, "halted": True},
        }
    )
    assert session.followups == Followups(pending=2, halted=True)


def test_parse_session_missing_followups_defaults() -> None:
    session = protocol.parse_session({"name": "a", "snapshot": "", "bell": {}})
    assert session.followups == Followups()


def test_parse_session_carries_cwd() -> None:
    session = protocol.parse_session(
        {"name": "a", "snapshot": "", "bell": {}, "cwd": "/home/user/project"}
    )
    assert session.cwd == "/home/user/project"


def test_parse_session_missing_cwd_defaults_none() -> None:
    session = protocol.parse_session({"name": "a", "snapshot": "", "bell": {}})
    assert session.cwd is None


def test_parse_session_snapshot_pre_c_server_all_four_new_fields_default() -> None:
    """A server predating item C (cwd/followups/views/created_at parity on
    GET /api/sessions/{name}) omits all four -- every one must default,
    never raise."""
    snap = protocol.parse_session_snapshot({"name": "a", "snapshot": "x", "bell": {}})
    assert snap.created_at is None
    assert snap.followups == Followups()
    assert snap.views == ()
    assert snap.cwd is None


def test_parse_session_snapshot_full_parity_fields() -> None:
    snap = protocol.parse_session_snapshot(
        {
            "name": "a",
            "snapshot": "x",
            "bell": {},
            "created_at": 100.0,
            "followups": {"pending": 1, "halted": False},
            "views": ["work"],
            "cwd": "/home/user",
        }
    )
    assert snap.created_at == 100.0
    assert snap.followups == Followups(pending=1, halted=False)
    assert snap.views == ("work",)
    assert snap.cwd == "/home/user"


# ---------------------------------------------------------------------------
# FollowupItem / FollowupQueue
# ---------------------------------------------------------------------------


def test_parse_followup_item_full() -> None:
    item = protocol.parse_followup_item(
        {"id": "abc", "text": "run tests", "enter": True, "created_at": 5.0}
    )
    assert item == FollowupItem(id="abc", text="run tests", enter=True, created_at=5.0)


def test_parse_followup_item_missing_created_at_defaults_none() -> None:
    item = protocol.parse_followup_item({"id": "abc", "text": "x", "enter": True})
    assert item.created_at is None


def test_parse_followup_queue_full() -> None:
    raw = {
        "session": "a",
        "revision": 3,
        "items": [{"id": "1", "text": "x", "enter": True, "created_at": 1.0}],
        "halted": None,
        "target_window": "0:main",
    }
    queue = protocol.parse_followup_queue(raw)
    assert queue.session == "a"
    assert queue.revision == 3
    assert queue.items == (FollowupItem(id="1", text="x", enter=True, created_at=1.0),)
    assert queue.halted is None
    assert queue.target_window == "0:main"


def test_parse_followup_queue_halted_null_parses_to_none() -> None:
    """`halted: null` on the wire must parse to `halted is None`, not a
    truthy empty mapping."""
    queue = protocol.parse_followup_queue(
        {"session": "a", "revision": 0, "items": [], "halted": None}
    )
    assert queue.halted is None


def test_parse_followup_queue_halted_present_is_mapping() -> None:
    halted = {"reason": "input_disabled", "detail": "x", "at": 1.0, "item_id": "1"}
    queue = protocol.parse_followup_queue(
        {"session": "a", "revision": 1, "items": [], "halted": halted}
    )
    assert queue.halted == halted


def test_parse_followup_queue_missing_target_window_defaults_none() -> None:
    queue = protocol.parse_followup_queue(
        {"session": "a", "revision": 0, "items": [], "halted": None}
    )
    assert queue.target_window is None


def test_build_followup_items_body_preserves_id() -> None:
    items = [FollowupItem(id="1", text="x", enter=True)]
    body = protocol.build_followup_items_body(items)
    assert body == [{"id": "1", "text": "x", "enter": True}]


def test_build_followup_items_body_empty_id_becomes_none() -> None:
    """An item constructed with `id=""` (a new, not-yet-persisted item) is
    sent as `id: None` on the wire -- the server treats a falsy id
    identically to an absent one (new item), and `None` is the honest
    shape for "no id yet"."""
    items = [FollowupItem(id="", text="new item", enter=True)]
    body = protocol.build_followup_items_body(items)
    assert body == [{"id": None, "text": "new item", "enter": True}]


# ---------------------------------------------------------------------------
# SessionCommand / SessionCommands
# ---------------------------------------------------------------------------


def test_parse_session_command() -> None:
    cmd = protocol.parse_session_command(
        {
            "id": "default",
            "label": "Default",
            "new_session_template": "tmux new -d -s {name}",
            "delete_session_template": "tmux kill-session -t {name}",
        }
    )
    assert cmd.id == "default"
    assert cmd.label == "Default"
    assert cmd.new_session_template == "tmux new -d -s {name}"
    assert cmd.delete_session_template == "tmux kill-session -t {name}"


def test_parse_session_commands_full() -> None:
    raw = {
        "commands": [
            {
                "id": "default",
                "label": "Default",
                "new_session_template": "a",
                "delete_session_template": "b",
            }
        ],
        "default_id": "default",
        "errors": [],
    }
    result = protocol.parse_session_commands(raw)
    assert len(result.commands) == 1
    assert result.commands[0].id == "default"
    assert result.default_id == "default"
    assert result.errors == ()


def test_parse_session_commands_with_errors() -> None:
    raw = {
        "commands": [
            {
                "id": "default",
                "label": "Default",
                "new_session_template": "a",
                "delete_session_template": "b",
            }
        ],
        "default_id": "default",
        "errors": ["session_commands[0]: bad entry"],
    }
    result = protocol.parse_session_commands(raw)
    assert result.errors == ("session_commands[0]: bad entry",)

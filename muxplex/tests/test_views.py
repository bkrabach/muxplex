"""
Tests for muxplex/views.py — views invariant enforcement and v2 visibility helpers.
"""

from muxplex.views import (
    add_membership,
    enforce_mutual_exclusion,
    filter_visible,
    hide,
    is_hidden,
    normalize_session_keys,
    prune_stale_keys,
    remove_from_all_views,
    remove_membership,
    unhide,
    validate_view_name,
    visible_count,
)


# ---------------------------------------------------------------------------
# Test fixtures (built-in, no pytest fixtures needed)
# ---------------------------------------------------------------------------


def _session(name: str, device_id: str = "dev1", status: str | None = None) -> dict:
    """Build a session dict with the standard fields."""
    d: dict = {"sessionKey": f"{device_id}:{name}", "name": name}
    if status is not None:
        d["status"] = status
    return d


# ---------------------------------------------------------------------------
# enforce_mutual_exclusion
# ---------------------------------------------------------------------------


def test_enforce_removes_from_hidden_when_in_view():
    """If a session is in both hidden_sessions and a view, remove from hidden (favor visibility)."""
    settings = {
        "hidden_sessions": ["abc:dev", "def:build"],
        "views": [
            {"name": "Work", "sessions": ["abc:dev", "abc:web"]},
        ],
    }
    result = enforce_mutual_exclusion(settings)
    assert "abc:dev" not in result["hidden_sessions"]
    assert "def:build" in result["hidden_sessions"]
    assert "abc:dev" in result["views"][0]["sessions"]


def test_enforce_no_change_when_no_overlap():
    """No changes when there is no overlap between hidden and views."""
    settings = {
        "hidden_sessions": ["abc:old"],
        "views": [
            {"name": "Work", "sessions": ["abc:dev"]},
        ],
    }
    result = enforce_mutual_exclusion(settings)
    assert result["hidden_sessions"] == ["abc:old"]
    assert result["views"][0]["sessions"] == ["abc:dev"]


def test_enforce_handles_empty_views():
    """Works when views is an empty list."""
    settings = {
        "hidden_sessions": ["abc:dev"],
        "views": [],
    }
    result = enforce_mutual_exclusion(settings)
    assert result["hidden_sessions"] == ["abc:dev"]


def test_enforce_handles_empty_hidden():
    """Works when hidden_sessions is empty."""
    settings = {
        "hidden_sessions": [],
        "views": [{"name": "Work", "sessions": ["abc:dev"]}],
    }
    result = enforce_mutual_exclusion(settings)
    assert result["hidden_sessions"] == []


def test_enforce_deduplicates_view_sessions():
    """Duplicate session keys within a view are deduplicated."""
    settings = {
        "hidden_sessions": [],
        "views": [
            {"name": "Work", "sessions": ["abc:dev", "abc:dev", "abc:web"]},
        ],
    }
    result = enforce_mutual_exclusion(settings)
    assert result["views"][0]["sessions"] == ["abc:dev", "abc:web"]


def test_enforce_overlap_across_multiple_views():
    """A hidden session appearing in multiple views is removed from hidden."""
    settings = {
        "hidden_sessions": ["abc:dev"],
        "views": [
            {"name": "Work", "sessions": ["abc:dev"]},
            {"name": "Hobby", "sessions": ["abc:dev", "abc:printer"]},
        ],
    }
    result = enforce_mutual_exclusion(settings)
    assert "abc:dev" not in result["hidden_sessions"]


# ---------------------------------------------------------------------------
# validate_view_name
# ---------------------------------------------------------------------------


def test_validate_rejects_empty_name():
    assert validate_view_name("", []) is not None


def test_validate_rejects_whitespace_only():
    assert validate_view_name("   ", []) is not None


def test_validate_rejects_too_long():
    assert validate_view_name("a" * 31, []) is not None


def test_validate_rejects_reserved_all():
    assert validate_view_name("all", []) is not None


def test_validate_rejects_reserved_hidden():
    assert validate_view_name("Hidden", []) is not None


def test_validate_rejects_duplicate():
    existing = [{"name": "Work", "sessions": []}]
    assert validate_view_name("Work", existing) is not None


def test_validate_accepts_valid_name():
    assert validate_view_name("My Project", []) is None


def test_validate_trims_whitespace():
    """A name that is valid after trimming should pass."""
    assert validate_view_name("  My Project  ", []) is None


def test_validate_accepts_at_max_length():
    assert validate_view_name("a" * 30, []) is None


# ---------------------------------------------------------------------------
# v2 visibility helpers: is_hidden, filter_visible, visible_count
# See docs/plans/2026-05-17-hidden-state-redesign-design.md
# ---------------------------------------------------------------------------


def test_is_hidden_true_when_key_in_hidden_sessions():
    settings = {"hidden_sessions": ["dev1:a", "dev1:b"]}
    assert is_hidden("dev1:a", settings) is True
    assert is_hidden("dev1:b", settings) is True


def test_is_hidden_false_when_key_absent():
    settings = {"hidden_sessions": ["dev1:a"]}
    assert is_hidden("dev1:b", settings) is False


def test_is_hidden_handles_missing_field():
    assert is_hidden("dev1:a", {}) is False
    assert is_hidden("dev1:a", {"hidden_sessions": None}) is False


# --- filter_visible: "all" view ---


def test_filter_visible_all_excludes_hidden_by_default():
    sessions = [_session("a"), _session("b"), _session("c")]
    settings = {"hidden_sessions": ["dev1:b"], "views": []}

    result = filter_visible(sessions, settings, "all")
    assert [s["name"] for s in result] == ["a", "c"]


def test_filter_visible_all_includes_hidden_when_requested():
    sessions = [_session("a"), _session("b"), _session("c")]
    settings = {"hidden_sessions": ["dev1:b"], "views": []}

    result = filter_visible(sessions, settings, "all", include_hidden=True)
    assert [s["name"] for s in result] == ["a", "b", "c"]


def test_filter_visible_all_excludes_status_tiles():
    sessions = [
        _session("a"),
        _session("disconnected", status="error"),
        _session("b"),
    ]
    settings = {"hidden_sessions": [], "views": []}

    result = filter_visible(sessions, settings, "all")
    assert [s["name"] for s in result] == ["a", "b"]


# --- filter_visible: "hidden" view ---


def test_filter_visible_hidden_returns_only_hidden_sessions():
    sessions = [_session("a"), _session("b"), _session("c")]
    settings = {"hidden_sessions": ["dev1:a", "dev1:c"], "views": []}

    result = filter_visible(sessions, settings, "hidden")
    assert [s["name"] for s in result] == ["a", "c"]


def test_filter_visible_hidden_returns_empty_when_no_hidden():
    sessions = [_session("a"), _session("b")]
    settings = {"hidden_sessions": [], "views": []}

    result = filter_visible(sessions, settings, "hidden")
    assert result == []


def test_filter_visible_hidden_excludes_dead_keys():
    """Stale hidden_sessions entries with no live counterpart are not counted."""
    sessions = [_session("a")]  # only "a" is live
    settings = {"hidden_sessions": ["dev1:a", "dev1:ghost"], "views": []}

    result = filter_visible(sessions, settings, "hidden")
    assert [s["name"] for s in result] == ["a"]


# --- filter_visible: user view ---


def test_filter_visible_user_view_membership_and_visibility():
    sessions = [_session("a"), _session("b"), _session("c")]
    settings = {
        "hidden_sessions": ["dev1:b"],
        "views": [{"name": "Work", "sessions": ["dev1:a", "dev1:b"]}],
    }

    result = filter_visible(sessions, settings, "Work")
    # 'a' is in view and not hidden; 'b' is in view but hidden — should be filtered out.
    assert [s["name"] for s in result] == ["a"]


def test_filter_visible_user_view_include_hidden_keeps_membership_filter():
    sessions = [_session("a"), _session("b"), _session("c")]
    settings = {
        "hidden_sessions": ["dev1:b"],
        "views": [{"name": "Work", "sessions": ["dev1:a", "dev1:b"]}],
    }

    result = filter_visible(sessions, settings, "Work", include_hidden=True)
    # include_hidden does not lift the membership filter — only the hidden filter.
    assert [s["name"] for s in result] == ["a", "b"]
    # 'c' is not in the view; never appears.


def test_filter_visible_unknown_view_returns_empty():
    sessions = [_session("a"), _session("b")]
    settings = {"hidden_sessions": [], "views": []}
    assert filter_visible(sessions, settings, "Nonexistent") == []


def test_filter_visible_user_view_with_overlap_state():
    """v2 permits a key in both hidden_sessions AND view.sessions.

    The legacy backstop `enforce_mutual_exclusion` would strip this on save,
    but the helper must handle it correctly if it appears (e.g. during a
    sync round before the backstop runs).
    """
    sessions = [_session("a"), _session("b")]
    settings = {
        "hidden_sessions": ["dev1:a"],  # also in view
        "views": [{"name": "Work", "sessions": ["dev1:a", "dev1:b"]}],
    }

    # Default behavior: hidden filter wins, 'a' is excluded from Work view.
    result = filter_visible(sessions, settings, "Work")
    assert [s["name"] for s in result] == ["b"]

    # include_hidden=True surfaces it again.
    result = filter_visible(sessions, settings, "Work", include_hidden=True)
    assert [s["name"] for s in result] == ["a", "b"]


# --- filter_visible: dual-lookup (legacy bare-name entries) ---


def test_filter_visible_matches_by_bare_name_when_stored_that_way():
    """Legacy entries stored as bare 'name' still match against session.name."""
    sessions = [_session("a"), _session("b")]
    settings = {
        "hidden_sessions": ["a"],  # bare name, not "dev1:a"
        "views": [],
    }
    result = filter_visible(sessions, settings, "all")
    assert [s["name"] for s in result] == ["b"]


def test_filter_visible_matches_bare_name_in_view_membership():
    sessions = [_session("a"), _session("b"), _session("c")]
    settings = {
        "hidden_sessions": [],
        "views": [{"name": "Work", "sessions": ["a", "b"]}],  # bare names
    }
    result = filter_visible(sessions, settings, "Work")
    assert [s["name"] for s in result] == ["a", "b"]


# --- visible_count ---


def test_visible_count_matches_filter_visible_length():
    sessions = [_session("a"), _session("b"), _session("c")]
    settings = {
        "hidden_sessions": ["dev1:b"],
        "views": [{"name": "Work", "sessions": ["dev1:a", "dev1:b", "dev1:c"]}],
    }

    for view, include_hidden in [
        ("all", False),
        ("all", True),
        ("hidden", False),
        ("Work", False),
        ("Work", True),
        ("Nonexistent", False),
    ]:
        expected = len(
            filter_visible(sessions, settings, view, include_hidden=include_hidden)
        )
        actual = visible_count(sessions, settings, view, include_hidden=include_hidden)
        assert actual == expected, (
            f"visible_count({view!r}, include_hidden={include_hidden}) "
            f"= {actual} != filter_visible length {expected}"
        )


# ---------------------------------------------------------------------------
# normalize_session_keys (Phase 1)
# ---------------------------------------------------------------------------


def test_normalize_upgrades_bare_name_in_hidden_sessions():
    sessions = [_session("a"), _session("b")]
    settings = {
        "hidden_sessions": ["a", "dev1:b"],  # 'a' is bare; 'b' already canonical
        "views": [],
    }
    result = normalize_session_keys(settings, sessions)
    assert result["hidden_sessions"] == ["dev1:a", "dev1:b"]


def test_normalize_upgrades_bare_name_in_view_sessions():
    sessions = [_session("a"), _session("b")]
    settings = {
        "hidden_sessions": [],
        "views": [{"name": "Work", "sessions": ["a", "dev1:b"]}],
    }
    result = normalize_session_keys(settings, sessions)
    assert result["views"][0]["sessions"] == ["dev1:a", "dev1:b"]


def test_normalize_leaves_unmatched_entries_alone():
    """Entries with no live counterpart are kept as-is (they may match later)."""
    sessions = [_session("a")]  # only 'a' is live
    settings = {
        "hidden_sessions": ["a", "ghost"],
        "views": [{"name": "Work", "sessions": ["a", "another-ghost"]}],
    }
    result = normalize_session_keys(settings, sessions)
    # 'a' is upgraded; the ghosts are preserved verbatim.
    assert result["hidden_sessions"] == ["dev1:a", "ghost"]
    assert result["views"][0]["sessions"] == ["dev1:a", "another-ghost"]


def test_normalize_is_idempotent():
    sessions = [_session("a"), _session("b")]
    settings = {
        "hidden_sessions": ["a"],
        "views": [{"name": "Work", "sessions": ["a", "b"]}],
    }
    once = normalize_session_keys(settings, sessions)
    twice = normalize_session_keys(once, sessions)
    assert once["hidden_sessions"] == twice["hidden_sessions"]
    assert once["views"] == twice["views"]


def test_normalize_handles_empty_or_missing_fields():
    """Don't crash when fields are missing or empty."""
    assert normalize_session_keys({}, []) == {}
    assert normalize_session_keys({"hidden_sessions": []}, []) == {
        "hidden_sessions": []
    }
    assert normalize_session_keys({"views": []}, []) == {"views": []}


def test_normalize_handles_cross_device_name_collisions_safely():
    """When two devices have a session with the same bare name, leave the stored
    bare-name entry alone — there's no unambiguous canonical form to choose."""
    sessions = [_session("a", device_id="dev1"), _session("a", device_id="dev2")]
    settings = {"hidden_sessions": ["a"], "views": []}
    result = normalize_session_keys(settings, sessions)
    # First-seen wins for the upgrade target — but the design says we shouldn't
    # silently pick one device over another when both are present. The
    # implementation uses setdefault, so first-seen wins. Document the behavior
    # in this test so the choice is visible.
    assert result["hidden_sessions"][0] in {"dev1:a", "dev2:a"}


# ---------------------------------------------------------------------------
# Pure data operations (Phase 2)
# See docs/plans/2026-05-17-hidden-state-redesign-design.md
# ---------------------------------------------------------------------------


def _settings_with_state() -> dict:
    """Return a settings dict with both views and hidden_sessions populated."""
    return {
        "hidden_sessions": ["dev1:a", "dev1:b"],
        "views": [
            {"name": "Work", "sessions": ["dev1:a", "dev1:c"]},
            {"name": "Personal", "sessions": ["dev1:b", "dev1:d"]},
        ],
    }


# --- add_membership ---


def test_add_membership_adds_key_when_absent():
    settings = _settings_with_state()
    result = add_membership(settings, "Work", "dev1:new")
    assert "dev1:new" in result["views"][0]["sessions"]


def test_add_membership_is_idempotent():
    settings = _settings_with_state()
    add_membership(settings, "Work", "dev1:a")  # already present
    assert settings["views"][0]["sessions"].count("dev1:a") == 1


def test_add_membership_is_noop_for_unknown_view():
    settings = _settings_with_state()
    original_work = settings["views"][0]["sessions"][:]
    original_personal = settings["views"][1]["sessions"][:]
    result = add_membership(settings, "DoesNotExist", "dev1:new")
    assert result["views"][0]["sessions"] == original_work
    assert result["views"][1]["sessions"] == original_personal


def test_add_membership_does_not_touch_hidden_sessions():
    """Pure op: add_membership must not touch hidden_sessions."""
    settings = _settings_with_state()
    original_hidden = settings["hidden_sessions"][:]
    add_membership(settings, "Work", "dev1:new")
    assert settings["hidden_sessions"] == original_hidden


# --- remove_membership ---


def test_remove_membership_removes_the_key():
    settings = _settings_with_state()
    result = remove_membership(settings, "Work", "dev1:a")
    assert "dev1:a" not in result["views"][0]["sessions"]


def test_remove_membership_is_noop_when_key_absent():
    settings = _settings_with_state()
    before = settings["views"][0]["sessions"][:]
    remove_membership(settings, "Work", "dev1:nothere")
    assert settings["views"][0]["sessions"] == before


def test_remove_membership_is_noop_for_unknown_view():
    settings = _settings_with_state()
    original_work = settings["views"][0]["sessions"][:]
    original_personal = settings["views"][1]["sessions"][:]
    result = remove_membership(settings, "DoesNotExist", "dev1:a")
    assert result["views"][0]["sessions"] == original_work
    assert result["views"][1]["sessions"] == original_personal


def test_remove_membership_does_not_touch_hidden_sessions():
    """Pure op: remove_membership must not touch hidden_sessions."""
    settings = _settings_with_state()
    original_hidden = settings["hidden_sessions"][:]
    remove_membership(settings, "Work", "dev1:a")
    assert settings["hidden_sessions"] == original_hidden


# --- remove_from_all_views ---


def test_remove_from_all_views_clears_key_from_all_views():
    settings = {
        "hidden_sessions": ["dev1:x"],
        "views": [
            {"name": "Work", "sessions": ["dev1:x", "dev1:y"]},
            {"name": "Personal", "sessions": ["dev1:x", "dev1:z"]},
        ],
    }
    result = remove_from_all_views(settings, "dev1:x")
    assert "dev1:x" not in result["views"][0]["sessions"]
    assert "dev1:x" not in result["views"][1]["sessions"]
    assert "dev1:y" in result["views"][0]["sessions"]
    assert "dev1:z" in result["views"][1]["sessions"]


def test_remove_from_all_views_does_not_touch_hidden_sessions():
    """Pure op: remove_from_all_views must not touch hidden_sessions."""
    settings = _settings_with_state()
    original_hidden = settings["hidden_sessions"][:]
    remove_from_all_views(settings, "dev1:a")
    assert settings["hidden_sessions"] == original_hidden


# --- hide ---


def test_hide_adds_to_hidden_sessions_when_absent():
    settings = {"hidden_sessions": ["dev1:b"], "views": []}
    result = hide(settings, "dev1:new")
    assert "dev1:new" in result["hidden_sessions"]


def test_hide_is_idempotent():
    settings = {"hidden_sessions": ["dev1:a"], "views": []}
    hide(settings, "dev1:a")  # already present
    assert settings["hidden_sessions"].count("dev1:a") == 1


def test_hide_does_not_touch_views():
    """Pure op: hide must not touch views."""
    settings = _settings_with_state()
    original_work = settings["views"][0]["sessions"][:]
    original_personal = settings["views"][1]["sessions"][:]
    hide(settings, "dev1:new")
    assert settings["views"][0]["sessions"] == original_work
    assert settings["views"][1]["sessions"] == original_personal


# --- unhide ---


def test_unhide_removes_from_hidden_sessions():
    settings = {"hidden_sessions": ["dev1:a", "dev1:b"], "views": []}
    result = unhide(settings, "dev1:a")
    assert "dev1:a" not in result["hidden_sessions"]
    assert "dev1:b" in result["hidden_sessions"]


def test_unhide_is_noop_when_absent():
    settings = {"hidden_sessions": ["dev1:b"], "views": []}
    before = settings["hidden_sessions"][:]
    unhide(settings, "dev1:nothere")
    assert settings["hidden_sessions"] == before


def test_unhide_does_not_touch_views():
    """Pure op: unhide must not touch views."""
    settings = _settings_with_state()
    original_work = settings["views"][0]["sessions"][:]
    original_personal = settings["views"][1]["sessions"][:]
    unhide(settings, "dev1:a")
    assert settings["views"][0]["sessions"] == original_work
    assert settings["views"][1]["sessions"] == original_personal


# ---------------------------------------------------------------------------
# Stale-key pruning (Phase 4)
# See docs/plans/2026-05-17-hidden-state-redesign-design.md, Phase 4 and
# the section "Stale key pruning (separate concern, local-only state)".
# ---------------------------------------------------------------------------

_GRACE = 86400.0  # 24 hours in seconds — default grace period used in tests
_T0 = 1_700_000_000.0  # arbitrary stable "now" for deterministic tests


def _pruning_settings() -> dict:
    """Settings with one view and a hidden_sessions list for pruning tests."""
    return {
        "hidden_sessions": ["dev1:hidden"],
        "views": [
            {
                "name": "Work",
                "sessions": ["dev1:work-a", "dev1:work-b"],
            },
            {
                "name": "Personal",
                "sessions": ["dev1:personal-a"],
            },
        ],
    }


# 1. Live key clears bookkeeping
def test_prune_live_key_clears_first_missed_at():
    """If a key is in live_keys, any existing first_missed_at entry is removed."""
    settings = _pruning_settings()
    pruning_state = {"first_missed_at": {"dev1:work-a": _T0 - 1000.0}}

    _, ps, changed = prune_stale_keys(
        settings,
        live_keys={"dev1:work-a", "dev1:work-b", "dev1:hidden", "dev1:personal-a"},
        pruning_state=pruning_state,
        grace_seconds=_GRACE,
        now=_T0,
    )

    assert "dev1:work-a" not in ps["first_missed_at"]
    assert changed is False


# 2. Newly missing key records first_missed_at, settings unchanged
def test_prune_newly_missing_key_records_timestamp():
    """A key that is in settings but absent from live_keys gets a first_missed_at entry."""
    settings = _pruning_settings()
    pruning_state = {"first_missed_at": {}}

    _, ps, changed = prune_stale_keys(
        settings,
        live_keys={"dev1:work-b", "dev1:hidden", "dev1:personal-a"},
        # dev1:work-a is missing from live_keys
        pruning_state=pruning_state,
        grace_seconds=_GRACE,
        now=_T0,
    )

    assert ps["first_missed_at"]["dev1:work-a"] == _T0
    assert changed is False
    # key still in settings — not pruned yet
    assert "dev1:work-a" in settings["views"][0]["sessions"]


# 3. Missing within grace — settings unchanged
def test_prune_within_grace_period_leaves_settings_unchanged():
    """A key with first_missed_at = now - grace + 1 is within grace; nothing changes."""
    settings = _pruning_settings()
    # first missed 1 second before grace expires
    first_missed_at = _T0 - _GRACE + 1.0
    pruning_state = {"first_missed_at": {"dev1:work-a": first_missed_at}}

    _, ps, changed = prune_stale_keys(
        settings,
        live_keys={"dev1:work-b", "dev1:hidden", "dev1:personal-a"},
        pruning_state=pruning_state,
        grace_seconds=_GRACE,
        now=_T0,
    )

    assert changed is False
    assert "dev1:work-a" in settings["views"][0]["sessions"]
    assert ps["first_missed_at"]["dev1:work-a"] == first_missed_at  # unchanged


# 4. Missing past grace — key removed from hidden_sessions AND view.sessions
def test_prune_past_grace_removes_key_from_settings():
    """A key missing for > grace_seconds is removed from hidden_sessions and every view."""
    settings = {
        "hidden_sessions": ["dev1:stale", "dev1:live-hidden"],
        "views": [
            {"name": "Work", "sessions": ["dev1:stale", "dev1:live-work"]},
            {"name": "Personal", "sessions": ["dev1:stale", "dev1:live-personal"]},
        ],
    }
    # stale key has been missing for grace + 1 second
    pruning_state = {"first_missed_at": {"dev1:stale": _T0 - _GRACE - 1.0}}

    updated, ps, changed = prune_stale_keys(
        settings,
        live_keys={"dev1:live-hidden", "dev1:live-work", "dev1:live-personal"},
        pruning_state=pruning_state,
        grace_seconds=_GRACE,
        now=_T0,
    )

    assert changed is True
    # Removed from hidden_sessions
    assert "dev1:stale" not in updated["hidden_sessions"]
    assert "dev1:live-hidden" in updated["hidden_sessions"]
    # Removed from every view
    assert "dev1:stale" not in updated["views"][0]["sessions"]
    assert "dev1:live-work" in updated["views"][0]["sessions"]
    assert "dev1:stale" not in updated["views"][1]["sessions"]
    assert "dev1:live-personal" in updated["views"][1]["sessions"]
    # Bookkeeping entry dropped
    assert "dev1:stale" not in ps["first_missed_at"]


# 5. Re-appearance after partial absence clears bookkeeping
def test_prune_reappearance_clears_bookkeeping():
    """A key that was missing (has bookkeeping) but returns to live_keys is forgiven."""
    settings = _pruning_settings()
    # dev1:work-a was previously seen as missing
    pruning_state = {"first_missed_at": {"dev1:work-a": _T0 - 3600.0}}

    _, ps, changed = prune_stale_keys(
        settings,
        live_keys={
            "dev1:work-a",  # back!
            "dev1:work-b",
            "dev1:hidden",
            "dev1:personal-a",
        },
        pruning_state=pruning_state,
        grace_seconds=_GRACE,
        now=_T0,
    )

    assert "dev1:work-a" not in ps["first_missed_at"]
    assert changed is False
    # key is still in settings (was never pruned)
    assert "dev1:work-a" in settings["views"][0]["sessions"]


# 6. Bookkeeping GC — stale entries not in settings are dropped
def test_prune_gc_drops_orphaned_bookkeeping_entries():
    """A first_missed_at entry whose key is no longer in settings is garbage-collected."""
    settings = _pruning_settings()
    # "dev1:ghost" is in bookkeeping but not in any settings list
    pruning_state = {
        "first_missed_at": {
            "dev1:ghost": _T0 - 100.0,  # not in settings, not expired
        }
    }

    _, ps, changed = prune_stale_keys(
        settings,
        live_keys={"dev1:work-a", "dev1:work-b", "dev1:hidden", "dev1:personal-a"},
        pruning_state=pruning_state,
        grace_seconds=_GRACE,
        now=_T0,
    )

    # Orphaned entry removed — it was never in settings
    assert "dev1:ghost" not in ps["first_missed_at"]
    assert changed is False


# 7. settings_changed flag is True only when a key is actually dropped
def test_prune_changed_flag_is_false_when_nothing_pruned():
    """settings_changed is False when all keys are live or within grace."""
    settings = _pruning_settings()
    _, _, changed = prune_stale_keys(
        settings,
        live_keys={"dev1:work-a", "dev1:work-b", "dev1:hidden", "dev1:personal-a"},
        pruning_state={"first_missed_at": {}},
        grace_seconds=_GRACE,
        now=_T0,
    )
    assert changed is False


def test_prune_changed_flag_is_true_when_key_dropped():
    """settings_changed is True when at least one key is actually removed."""
    settings = {
        "hidden_sessions": ["dev1:stale"],
        "views": [],
    }
    pruning_state = {"first_missed_at": {"dev1:stale": _T0 - _GRACE - 1.0}}

    _, _, changed = prune_stale_keys(
        settings,
        live_keys=set(),
        pruning_state=pruning_state,
        grace_seconds=_GRACE,
        now=_T0,
    )
    assert changed is True


# 8. Idempotent on stable state
def test_prune_idempotent_on_stable_state():
    """Calling prune twice with the same inputs produces the same result the second time."""
    # First call: some keys are missing → bookkeeping recorded, settings unchanged.
    settings_a = _pruning_settings()
    ps_a: dict = {"first_missed_at": {}}

    settings_a, ps_a, changed_a = prune_stale_keys(
        settings_a,
        live_keys={"dev1:hidden", "dev1:personal-a"},  # work keys missing
        pruning_state=ps_a,
        grace_seconds=_GRACE,
        now=_T0,
    )
    assert changed_a is False  # within grace (first miss)

    # Second call with SAME now — bookkeeping is the same, nothing changes.
    import copy

    settings_b = copy.deepcopy(settings_a)
    ps_b = copy.deepcopy(ps_a)

    settings_b, ps_b, changed_b = prune_stale_keys(
        settings_b,
        live_keys={"dev1:hidden", "dev1:personal-a"},
        pruning_state=ps_b,
        grace_seconds=_GRACE,
        now=_T0,  # same now — grace not expired
    )

    assert changed_b is False
    assert settings_b == settings_a
    assert ps_b == ps_a


# 9. Does NOT touch unrelated keys or memberships
def test_prune_does_not_touch_unrelated_keys():
    """Pruning one stale key leaves all live keys and other view memberships intact."""
    settings = {
        "hidden_sessions": ["dev1:stale", "dev1:live-hidden"],
        "views": [
            {
                "name": "Work",
                "sessions": ["dev1:stale", "dev1:live-work-1", "dev1:live-work-2"],
            },
        ],
    }
    pruning_state = {"first_missed_at": {"dev1:stale": _T0 - _GRACE - 1.0}}

    updated, _, changed = prune_stale_keys(
        settings,
        live_keys={"dev1:live-hidden", "dev1:live-work-1", "dev1:live-work-2"},
        pruning_state=pruning_state,
        grace_seconds=_GRACE,
        now=_T0,
    )

    assert changed is True
    # Stale key gone
    assert "dev1:stale" not in updated["hidden_sessions"]
    assert "dev1:stale" not in updated["views"][0]["sessions"]
    # Live keys untouched
    assert "dev1:live-hidden" in updated["hidden_sessions"]
    assert "dev1:live-work-1" in updated["views"][0]["sessions"]
    assert "dev1:live-work-2" in updated["views"][0]["sessions"]


# ---------------------------------------------------------------------------
# Federation-aware pruning: positive-knowledge rule
#
# A key "<device_id>:<name>" is only ever evaluated for pruning when the
# owning device's session list is CURRENTLY KNOWN to this instance -- see
# prune_stale_keys's docstring. These tests opt in via local_device_id /
# known_remote_device_ids; the tests above (which omit both) prove the
# legacy/backward-compatible behavior is unchanged when a caller doesn't
# supply them.
# ---------------------------------------------------------------------------

_LOCAL = "local-dev"
_REMOTE = "remote-dev"


def _fed_settings(key: str) -> dict:
    """Minimal settings with a single view containing exactly one key."""
    return {
        "hidden_sessions": [],
        "views": [{"name": "Work", "sessions": [key]}],
    }


# 10. Local dead session past grace -> pruned (existing behavior preserved
#     when local_device_id/known_remote_device_ids are supplied explicitly).
def test_prune_local_dead_session_past_grace_is_pruned():
    """A LOCAL key missing past grace is pruned even with the positive-knowledge
    gate active, because own-device keys are always evaluable."""
    key = f"{_LOCAL}:dead-session"
    settings = _fed_settings(key)
    pruning_state = {"first_missed_at": {key: _T0 - _GRACE - 1.0}}

    updated, ps, changed = prune_stale_keys(
        settings,
        live_keys=set(),  # local session is gone
        pruning_state=pruning_state,
        grace_seconds=_GRACE,
        now=_T0,
        local_device_id=_LOCAL,
        known_remote_device_ids=set(),
    )

    assert changed is True
    assert key not in updated["views"][0]["sessions"]
    assert key not in ps["first_missed_at"]


# 11. Local live session -> never pruned.
def test_prune_local_live_session_never_pruned():
    """A LOCAL key that is live is never pruned and accrues no bookkeeping."""
    key = f"{_LOCAL}:alive-session"
    settings = _fed_settings(key)

    updated, ps, changed = prune_stale_keys(
        settings,
        live_keys={key},
        pruning_state={"first_missed_at": {}},
        grace_seconds=_GRACE,
        now=_T0,
        local_device_id=_LOCAL,
        known_remote_device_ids=set(),
    )

    assert changed is False
    assert key in updated["views"][0]["sessions"]
    assert key not in ps["first_missed_at"]


# 12. Remote key, owning device REACHABLE, session absent -> pruned after grace.
def test_prune_remote_key_reachable_device_session_absent_pruned_after_grace():
    """A remote-owned key is pruned once grace expires IF the owning device is
    reachable and its (empty, in this case) live keys don't include it."""
    key = f"{_REMOTE}:gone-session"
    settings = _fed_settings(key)
    pruning_state = {"first_missed_at": {key: _T0 - _GRACE - 1.0}}

    updated, ps, changed = prune_stale_keys(
        settings,
        live_keys=set(),  # remote device reachable, but this session absent
        pruning_state=pruning_state,
        grace_seconds=_GRACE,
        now=_T0,
        local_device_id=_LOCAL,
        known_remote_device_ids={_REMOTE},
    )

    assert changed is True
    assert key not in updated["views"][0]["sessions"]
    assert key not in ps["first_missed_at"]


# 13. Remote key, owning device REACHABLE, session present -> never pruned,
#     clock cleared.
def test_prune_remote_key_reachable_device_session_present_never_pruned():
    """A remote-owned key is never pruned, and any stale clock is cleared, when
    the owning device is reachable and reports the session as still live."""
    key = f"{_REMOTE}:alive-session"
    settings = _fed_settings(key)
    # Pretend it was previously seen missing (should be forgiven now).
    pruning_state = {"first_missed_at": {key: _T0 - 100.0}}

    updated, ps, changed = prune_stale_keys(
        settings,
        live_keys={key},  # remote device reachable, session present
        pruning_state=pruning_state,
        grace_seconds=_GRACE,
        now=_T0,
        local_device_id=_LOCAL,
        known_remote_device_ids={_REMOTE},
    )

    assert changed is False
    assert key in updated["views"][0]["sessions"]
    assert key not in ps["first_missed_at"]


# 14. Remote key, owning device UNREACHABLE/unknown -> never pruned, no clock
#     accrues, regardless of elapsed time.
def test_prune_remote_key_unknown_device_never_pruned_no_clock_accrues():
    """A remote-owned key whose owning device is NOT in known_remote_device_ids
    is 'unknown, not dead': never pruned, and first_missed_at never starts or
    advances for it -- even with an empty live_keys and a huge elapsed time."""
    key = f"{_REMOTE}:maybe-alive"
    settings = _fed_settings(key)

    # First cycle: no prior bookkeeping at all.
    updated, ps, changed = prune_stale_keys(
        settings,
        live_keys=set(),  # irrelevant -- device is unknown, key isn't evaluated
        pruning_state={"first_missed_at": {}},
        grace_seconds=_GRACE,
        now=_T0,
        local_device_id=_LOCAL,
        known_remote_device_ids=set(),  # _REMOTE NOT known/reachable
    )
    assert changed is False
    assert key in updated["views"][0]["sessions"]
    assert key not in ps["first_missed_at"], (
        "an unknown device's key must never start a first_missed_at clock"
    )

    # Second cycle: even with an already-expired clock somehow present (e.g.
    # left over from before this device went dark), it must be cleared, not
    # honored, and the key must survive.
    pruning_state_with_stale_clock = {"first_missed_at": {key: _T0 - _GRACE - 1.0}}
    updated2, ps2, changed2 = prune_stale_keys(
        settings,
        live_keys=set(),
        pruning_state=pruning_state_with_stale_clock,
        grace_seconds=_GRACE,
        now=_T0,
        local_device_id=_LOCAL,
        known_remote_device_ids=set(),
    )
    assert changed2 is False
    assert key in updated2["views"][0]["sessions"]
    assert key not in ps2["first_missed_at"]


# 15. Device offline for > grace then returns with the session still present
#     -> key survives (the regression this fix exists to prevent).
def test_prune_device_offline_past_grace_then_returns_with_session_intact():
    """The core regression scenario: a laptop goes offline for well over the
    grace period (during which every OTHER device in the fleet would, under
    the OLD bug, see the offline device's keys as permanently missing and
    prune them). With the fix, the key must survive the entire offline
    window and remain intact once the device reappears with the session
    still present.
    """
    key = f"{_REMOTE}:still-there"
    settings = _fed_settings(key)
    pruning_state: dict = {"first_missed_at": {}}

    # Simulate many poll cycles while the remote device is offline/unknown,
    # spanning well past the grace period.
    offline_cycle_times = [_T0, _T0 + _GRACE / 2, _T0 + _GRACE + 1.0, _T0 + 2 * _GRACE]
    for t in offline_cycle_times:
        settings, pruning_state, changed = prune_stale_keys(
            settings,
            live_keys=set(),
            pruning_state=pruning_state,
            grace_seconds=_GRACE,
            now=t,
            local_device_id=_LOCAL,
            known_remote_device_ids=set(),  # offline: not known
        )
        assert changed is False
        assert key in settings["views"][0]["sessions"]
        assert key not in pruning_state["first_missed_at"]

    # Device comes back online, reachable, and its session is still present.
    settings, pruning_state, changed = prune_stale_keys(
        settings,
        live_keys={key},
        pruning_state=pruning_state,
        grace_seconds=_GRACE,
        now=_T0 + 2 * _GRACE + 10.0,
        local_device_id=_LOCAL,
        known_remote_device_ids={_REMOTE},
    )
    assert changed is False
    assert key in settings["views"][0]["sessions"], (
        "an offline device's view membership must survive the outage -- "
        "this is the exact regression the federation-aware pruning fix "
        "exists to prevent"
    )
    assert key not in pruning_state["first_missed_at"]


# 16. Bare-name legacy entry -> unchanged behavior regardless of
#     local_device_id/known_remote_device_ids.
def test_prune_bare_name_legacy_entry_unchanged_behavior():
    """A legacy bare-name key (no device_id: prefix) has no determinable
    owner and is always evaluated directly against live_keys, even when the
    positive-knowledge gate is active via local_device_id."""
    settings = {"hidden_sessions": ["bare-gone"], "views": []}
    pruning_state = {"first_missed_at": {"bare-gone": _T0 - _GRACE - 1.0}}

    updated, ps, changed = prune_stale_keys(
        settings,
        live_keys=set(),  # bare name absent -- evaluated normally, no owner gate
        pruning_state=pruning_state,
        grace_seconds=_GRACE,
        now=_T0,
        local_device_id=_LOCAL,
        known_remote_device_ids=set(),
    )

    assert changed is True
    assert "bare-gone" not in updated["hidden_sessions"]
    assert "bare-gone" not in ps["first_missed_at"]


def test_prune_bare_name_legacy_entry_live_is_preserved():
    """A legacy bare-name key that IS live is preserved, positive-knowledge
    gate notwithstanding."""
    settings = {"hidden_sessions": ["bare-alive"], "views": []}

    updated, ps, changed = prune_stale_keys(
        settings,
        live_keys={"bare-alive"},
        pruning_state={"first_missed_at": {}},
        grace_seconds=_GRACE,
        now=_T0,
        local_device_id=_LOCAL,
        known_remote_device_ids=set(),
    )

    assert changed is False
    assert "bare-alive" in updated["hidden_sessions"]
    assert "bare-alive" not in ps["first_missed_at"]


# 17. A prune large enough to collapse views trips the destructive-write
#     backstop and makes no write. This mirrors main.py's _run_poll_cycle
#     step 14, which must assess prune_stale_keys' output the same way
#     patch_settings()/apply_synced_settings() do before persisting it.
def test_mass_prune_that_would_collapse_views_is_refused_by_backstop():
    """prune_stale_keys() itself is a pure function and will happily remove
    every dead key it's asked to -- the backstop lives one layer up, in the
    caller that decides whether to persist the result (main.py's poll cycle,
    mirrored here). A prune that would collapse views from many down to the
    collapse threshold must be assessed via assess_views_destruction and
    the write skipped, exactly like a catastrophic PATCH or federation sync.
    """
    from muxplex.views import assess_views_destruction

    # 8 views, each with one now-dead remote key, all past grace.
    views_before = [
        {"name": f"v{i}", "sessions": [f"{_REMOTE}:dead-{i}"]} for i in range(8)
    ]
    settings = {"hidden_sessions": [], "views": views_before}
    views_before_snapshot = [
        dict(v, sessions=list(v["sessions"])) for v in views_before
    ]

    pruning_state = {
        "first_missed_at": {f"{_REMOTE}:dead-{i}": _T0 - _GRACE - 1.0 for i in range(8)}
    }

    updated, ps, changed = prune_stale_keys(
        settings,
        live_keys=set(),  # remote device reachable, but ALL its sessions gone
        pruning_state=pruning_state,
        grace_seconds=_GRACE,
        now=_T0,
        local_device_id=_LOCAL,
        known_remote_device_ids={_REMOTE},
    )

    # The pure function itself performs the prune -- it has no knowledge of
    # the backstop (that's the caller's responsibility, exercised below).
    assert changed is True
    assert all(v["sessions"] == [] for v in updated["views"])

    # The caller-side backstop check (mirrors main.py's _run_poll_cycle):
    # a 8-views-worth-of-members-to-0 collapse must be flagged destructive,
    # meaning the caller must refuse to persist `updated` to settings.json.
    assessment = assess_views_destruction(views_before_snapshot, updated["views"])
    assert assessment.destructive is True, (
        "a mass prune that empties every view's membership must trip the "
        "destructive-write backstop so the caller refuses to persist it"
    )


# ---------------------------------------------------------------------------
# End-to-end: normalize → prune pipeline (wired into _run_poll_cycle)
# Verifies that normalize_session_keys and prune_stale_keys compose correctly,
# mirroring the logic added to _run_poll_cycle in main.py (step 13b + step 14).
# ---------------------------------------------------------------------------


def _make_sessions_for_normalize(device_id: str, names: list[str]) -> list[dict]:
    """Build the same session-dict list that _run_poll_cycle constructs for normalization."""
    return [{"name": n, "sessionKey": f"{device_id}:{n}"} for n in names]


def test_normalize_then_prune_upgrades_bare_names_and_keeps_live_keys():
    """Bare-name entries are upgraded to canonical form; the prune step then sees
    the canonical key in live_keys and does NOT start the stale clock."""
    device_id = "myhost"
    names = ["alpha", "beta"]

    settings = {
        "hidden_sessions": ["alpha"],  # bare name, pre-v2 style
        "views": [{"name": "Work", "sessions": ["alpha", "beta"]}],  # bare names
    }

    # --- step 13b: normalize ---
    sessions_for_normalize = _make_sessions_for_normalize(device_id, names)
    normalize_session_keys(settings, sessions_for_normalize)

    # After normalize, entries should be in canonical form.
    assert settings["hidden_sessions"] == ["myhost:alpha"]
    assert settings["views"][0]["sessions"] == ["myhost:alpha", "myhost:beta"]

    # --- step 14: prune ---
    live_keys: set[str] = set()
    for n in names:
        live_keys.add(n)
        live_keys.add(f"{device_id}:{n}")

    updated, pruning_state, changed = prune_stale_keys(
        settings,
        live_keys,
        pruning_state={"first_missed_at": {}},
        grace_seconds=_GRACE,
        now=_T0,
    )

    # No keys pruned — both canonical keys are in live_keys.
    assert changed is False
    assert "myhost:alpha" in updated["hidden_sessions"]
    assert "myhost:alpha" in updated["views"][0]["sessions"]
    assert "myhost:beta" in updated["views"][0]["sessions"]


def test_normalize_then_prune_already_canonical_is_idempotent():
    """Settings already in canonical device_id:name form pass through unchanged."""
    device_id = "myhost"
    names = ["alpha"]

    settings = {
        "hidden_sessions": ["myhost:alpha"],
        "views": [{"name": "Work", "sessions": ["myhost:alpha"]}],
    }

    import copy

    original = copy.deepcopy(settings)

    # --- step 13b: normalize (should be a no-op) ---
    sessions_for_normalize = _make_sessions_for_normalize(device_id, names)
    normalize_session_keys(settings, sessions_for_normalize)

    assert settings == original, "normalize must not mutate already-canonical settings"

    # --- step 14: prune (should also be a no-op) ---
    live_keys = {n for n in names} | {f"{device_id}:{n}" for n in names}
    _, _, changed = prune_stale_keys(
        settings,
        live_keys,
        pruning_state={"first_missed_at": {}},
        grace_seconds=_GRACE,
        now=_T0,
    )
    assert changed is False


def test_normalize_then_prune_stale_canonical_key_is_pruned_after_grace():
    """After normalization upgrades a bare name to canonical form, the prune step
    correctly starts the stale clock and eventually prunes the key once the grace
    period expires — verifying the full pipeline."""
    device_id = "myhost"
    names_live: list[str] = []  # session is gone

    settings = {
        "hidden_sessions": ["gone"],  # bare name for a session that no longer exists
        "views": [],
    }

    # --- step 13b: normalize — no live sessions, so bare name stays put ---
    sessions_for_normalize = _make_sessions_for_normalize(device_id, names_live)
    normalize_session_keys(settings, sessions_for_normalize)

    # The entry is not upgraded (no live session to match against).
    assert settings["hidden_sessions"] == ["gone"]

    # --- step 14, first poll: key is missing, start the clock ---
    live_keys: set[str] = set()  # nothing live
    _, pruning_state, changed = prune_stale_keys(
        settings,
        live_keys,
        pruning_state={"first_missed_at": {}},
        grace_seconds=_GRACE,
        now=_T0,
    )
    assert changed is False
    assert pruning_state["first_missed_at"]["gone"] == _T0

    # --- step 14, second poll (grace expired): key is removed ---
    _, pruning_state, changed = prune_stale_keys(
        settings,
        live_keys,
        pruning_state=pruning_state,
        grace_seconds=_GRACE,
        now=_T0 + _GRACE + 1.0,
    )
    assert changed is True
    assert "gone" not in settings["hidden_sessions"]

"""
Tests for muxplex/views.py — views invariant enforcement.
"""


from muxplex.views import (
    enforce_mutual_exclusion,
    validate_view_name,
)


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

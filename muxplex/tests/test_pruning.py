"""
Tests for muxplex/pruning.py — local sidecar bookkeeping for stale-key pruning.

The pruning sidecar (pruning.json) is NEVER synced to peers.  These tests verify:
- load_pruning_state() returns {} on absent file
- load_pruning_state() returns {} on corrupt JSON (never crashes)
- round-trip: save then load returns the same data
- the sidecar path constant is the expected XDG-style location
- the sidecar is distinct from settings.json (different constant, different path)
"""

import json
from pathlib import Path

import pytest

import muxplex.pruning as pruning_mod
from muxplex.pruning import load_pruning_state, save_pruning_state


# ---------------------------------------------------------------------------
# PRUNING_STATE_PATH isolation is NOT this module's job any more.
#
# conftest.py's autouse `_isolate_pruning_state_path` redirects it for EVERY
# test in the suite and returns the path, so tests that want to assert on the
# sidecar take it (via `pruning_sidecar` below) rather than re-redirecting it.
# The local copy this file used to carry protected only the tests that
# remembered to live here -- and a divert-probe found 13 writes reaching the
# real ~/.config/muxplex/pruning.json from modules that had no such copy.
# ---------------------------------------------------------------------------


@pytest.fixture
def pruning_sidecar(_isolate_pruning_state_path):
    """The isolated sidecar path, with its parent directory created.

    conftest's rail deliberately does NOT create the directory: a fresh host
    has none, and `save_pruning_state()` creating it is itself a tested
    behaviour (see `test_save_creates_parent_directories`). Tests that plant
    file contents directly, without going through the writer, need the parent
    to exist first -- that is this fixture's whole job.
    """
    _isolate_pruning_state_path.parent.mkdir(parents=True, exist_ok=True)
    return _isolate_pruning_state_path


# ---------------------------------------------------------------------------
# Default path check (against the module constant, not the redirected one)
# ---------------------------------------------------------------------------


def test_pruning_state_path_is_expected_location():
    """PRUNING_STATE_PATH must be ~/.config/muxplex/pruning.json."""
    # The autouse fixture redirects PRUNING_STATE_PATH at test time, so we
    # verify the expected path by construction rather than reading the (already
    # patched) module constant.
    expected = Path.home() / ".config" / "muxplex" / "pruning.json"
    # Path structure: ~/.config/muxplex/pruning.json
    assert expected.name == "pruning.json"
    assert expected.parent.name == "muxplex"
    assert expected.parent.parent.name == ".config"
    assert expected.parent.parent.parent == Path.home()


def test_pruning_state_path_differs_from_settings_path():
    """PRUNING_STATE_PATH and SETTINGS_PATH must be different files."""
    from muxplex.settings import SETTINGS_PATH

    pruning_default = Path.home() / ".config" / "muxplex" / "pruning.json"
    assert pruning_default != SETTINGS_PATH, (
        "pruning.json and settings.json must be distinct files — "
        "pruning bookkeeping must never be mixed with syncable settings"
    )


# ---------------------------------------------------------------------------
# load_pruning_state — missing file
# ---------------------------------------------------------------------------


def test_load_pruning_state_returns_empty_when_file_absent():
    """load_pruning_state() returns {} when the sidecar file does not exist."""
    # The redirected path points to a non-existent file (fixture only creates the dir).
    result = load_pruning_state()
    assert result == {}, (
        f"load_pruning_state() must return {{}} for absent file, got: {result!r}"
    )


# ---------------------------------------------------------------------------
# load_pruning_state — corrupt JSON
# ---------------------------------------------------------------------------


def test_load_pruning_state_returns_empty_on_corrupt_json(pruning_sidecar):
    """load_pruning_state() returns {} on corrupt JSON — never raises."""
    pruning_sidecar.write_text("NOT VALID JSON {{{{")

    result = load_pruning_state()

    assert result == {}, (
        f"load_pruning_state() must return {{}} on corrupt JSON, got: {result!r}"
    )


def test_load_pruning_state_returns_empty_on_truncated_file(
    pruning_sidecar,
):
    """load_pruning_state() returns {} on a file with stray/truncated bytes."""
    pruning_sidecar.write_bytes(b"\xff\xfe truncated")

    result = load_pruning_state()

    assert result == {}, (
        f"load_pruning_state() must return {{}} on stray bytes, got: {result!r}"
    )


def test_load_pruning_state_returns_empty_on_non_dict_json(
    pruning_sidecar,
):
    """load_pruning_state() returns {} when JSON parses to a non-dict (e.g. a list)."""
    pruning_sidecar.write_text(json.dumps([1, 2, 3]))

    result = load_pruning_state()

    assert result == {}, (
        f"load_pruning_state() must return {{}} when JSON root is not a dict, "
        f"got: {result!r}"
    )


# ---------------------------------------------------------------------------
# Round-trip: save then load
# ---------------------------------------------------------------------------


def test_save_then_load_round_trip():
    """save_pruning_state then load_pruning_state returns the same data."""
    state = {
        "first_missed_at": {
            "dev1:dead-session": 1747512345.0,
            "dev2:another-gone": 1747512000.0,
        }
    }

    save_pruning_state(state)
    loaded = load_pruning_state()

    assert loaded == state, (
        f"round-trip save/load must preserve data exactly; got: {loaded!r}"
    )


def test_save_creates_parent_directories(tmp_path, monkeypatch):
    """save_pruning_state creates parent directories as needed."""
    nested_path = tmp_path / "a" / "b" / "pruning.json"
    monkeypatch.setattr(pruning_mod, "PRUNING_STATE_PATH", nested_path)

    save_pruning_state({"first_missed_at": {}})

    assert nested_path.exists(), "save_pruning_state must create parent directories"


def test_save_writes_valid_json(pruning_sidecar):
    """save_pruning_state writes well-formed JSON (parseable by json.loads)."""
    state = {"first_missed_at": {"dev1:x": 1234567890.0}}
    save_pruning_state(state)

    raw = pruning_sidecar.read_text()
    parsed = json.loads(raw)
    assert parsed == state


def test_save_empty_state_round_trips(pruning_sidecar):
    """An empty pruning state saves and loads cleanly."""
    save_pruning_state({})
    loaded = load_pruning_state()
    assert loaded == {}


def test_save_overwrites_previous_state(pruning_sidecar):
    """Subsequent saves overwrite the previous sidecar contents."""
    save_pruning_state({"first_missed_at": {"dev1:old": 111.0}})
    save_pruning_state({"first_missed_at": {"dev1:new": 222.0}})

    loaded = load_pruning_state()
    assert loaded == {"first_missed_at": {"dev1:new": 222.0}}, (
        f"save must overwrite previous state; got: {loaded!r}"
    )

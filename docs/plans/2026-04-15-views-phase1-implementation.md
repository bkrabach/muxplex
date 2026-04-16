# Views Feature — Phase 1: Stable Device Identity + Data Model

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Establish stable device identity and data model changes so the backend and data layer are ready for the Views UI work in Phase 2.

**Architecture:** Each muxplex instance gets a persistent UUID stored in `~/.config/muxplex/identity.json` (outside federation sync boundary). All session keys change from positional `remoteId:name` to `device_id:name` uniformly for both local and remote sessions. Settings gain a `views` array (synced) and state gains an `active_view` field (per-device, not synced). Federation proxy endpoints switch from integer index to device_id string lookup.

**Tech Stack:** Python 3.12+ / FastAPI / pytest + pytest-asyncio / vanilla JS

**Design reference:** `docs/plans/2026-04-15-views-design.md`

---

## Task 1: Create `muxplex/identity.py` — Device Identity Module

**Files:**
- Create: `muxplex/identity.py`
- Create: `muxplex/tests/test_identity.py`

**Step 1: Write the failing tests**

Create `muxplex/tests/test_identity.py`:

```python
"""
Tests for muxplex/identity.py — device identity management.
"""

import json
import uuid
from pathlib import Path

import pytest

import muxplex.identity as identity_mod
from muxplex.identity import (
    IDENTITY_PATH,
    load_device_id,
    reset_device_id,
)


# ---------------------------------------------------------------------------
# Autouse fixture: redirect IDENTITY_PATH to tmp_path
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def redirect_identity_path(tmp_path, monkeypatch):
    """Redirect IDENTITY_PATH to a temporary file for all tests."""
    fake_path = tmp_path / "identity.json"
    monkeypatch.setattr(identity_mod, "IDENTITY_PATH", fake_path)
    return fake_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_load_creates_file_when_absent(tmp_path, monkeypatch):
    """load_device_id() creates identity.json with a valid UUID when no file exists."""
    fake_path = tmp_path / "identity.json"
    monkeypatch.setattr(identity_mod, "IDENTITY_PATH", fake_path)

    device_id = load_device_id()

    assert fake_path.exists()
    # Must be a valid UUID
    uuid.UUID(device_id)


def test_load_returns_same_id_on_repeated_calls(tmp_path, monkeypatch):
    """load_device_id() returns the same device_id on repeated calls."""
    fake_path = tmp_path / "identity.json"
    monkeypatch.setattr(identity_mod, "IDENTITY_PATH", fake_path)

    first = load_device_id()
    second = load_device_id()
    assert first == second


def test_load_reads_existing_file(tmp_path, monkeypatch):
    """load_device_id() reads an existing identity.json without overwriting."""
    fake_path = tmp_path / "identity.json"
    monkeypatch.setattr(identity_mod, "IDENTITY_PATH", fake_path)
    expected_id = str(uuid.uuid4())
    fake_path.write_text(json.dumps({"device_id": expected_id}))

    assert load_device_id() == expected_id


def test_load_creates_parent_dirs(tmp_path, monkeypatch):
    """load_device_id() creates parent directories if needed."""
    nested_path = tmp_path / "a" / "b" / "identity.json"
    monkeypatch.setattr(identity_mod, "IDENTITY_PATH", nested_path)

    load_device_id()
    assert nested_path.exists()


def test_load_regenerates_on_corrupt_json(tmp_path, monkeypatch):
    """load_device_id() generates a new id when identity.json is corrupt."""
    fake_path = tmp_path / "identity.json"
    monkeypatch.setattr(identity_mod, "IDENTITY_PATH", fake_path)
    fake_path.write_text("not valid json{{{")

    device_id = load_device_id()
    uuid.UUID(device_id)  # must be valid


def test_load_regenerates_on_missing_key(tmp_path, monkeypatch):
    """load_device_id() generates a new id when device_id key is missing from JSON."""
    fake_path = tmp_path / "identity.json"
    monkeypatch.setattr(identity_mod, "IDENTITY_PATH", fake_path)
    fake_path.write_text(json.dumps({"other_key": "value"}))

    device_id = load_device_id()
    uuid.UUID(device_id)  # must be valid


def test_reset_generates_new_id(tmp_path, monkeypatch):
    """reset_device_id() writes a new UUID different from the previous one."""
    fake_path = tmp_path / "identity.json"
    monkeypatch.setattr(identity_mod, "IDENTITY_PATH", fake_path)

    original = load_device_id()
    new_id = reset_device_id()

    assert new_id != original
    uuid.UUID(new_id)  # must be valid
    # File on disk must match
    data = json.loads(fake_path.read_text())
    assert data["device_id"] == new_id
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_identity.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'muxplex.identity'`

**Step 3: Write the implementation**

Create `muxplex/identity.py`:

```python
"""
Device identity management for muxplex.

Each muxplex instance gets a persistent device_id (UUID v4) stored in
~/.config/muxplex/identity.json. This file is explicitly outside the
federation settings sync boundary — it is never synced, never overwritten
by settings propagation.

The device_id is generated once on first startup and never regenerated
automatically. The --reset-device-id CLI command can generate a new one
for the "I copied my dotfiles" scenario.
"""

import json
import uuid
from pathlib import Path

IDENTITY_PATH = Path.home() / ".config" / "muxplex" / "identity.json"


def load_device_id() -> str:
    """Load the device_id from identity.json, generating one if absent.

    Creates the file and parent directories on first call.
    Regenerates if the file is corrupt or missing the device_id key.
    """
    try:
        data = json.loads(IDENTITY_PATH.read_text())
        device_id = data.get("device_id", "")
        if device_id:
            return device_id
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    # Generate and persist a new device_id
    return reset_device_id()


def reset_device_id() -> str:
    """Generate a new device_id, write it to identity.json, and return it.

    Overwrites any existing device_id. Creates parent directories if needed.
    """
    device_id = str(uuid.uuid4())
    IDENTITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    IDENTITY_PATH.write_text(json.dumps({"device_id": device_id}, indent=2) + "\n")
    return device_id
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_identity.py -v
```
Expected: All 8 tests PASS

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/identity.py muxplex/tests/test_identity.py && git commit -m "feat: add identity.py for stable device identity (UUID in identity.json)"
```

---

## Task 2: Update `state.py` — New `muxplex` State Path + `active_view` Field

**Files:**
- Modify: `muxplex/state.py`
- Modify: `muxplex/tests/test_state.py`

**Step 1: Write the failing tests**

Add to the end of `muxplex/tests/test_state.py`:

```python
# ---------------------------------------------------------------------------
# active_view field in empty_state
# ---------------------------------------------------------------------------


def test_empty_state_has_active_view_key():
    state = empty_state()
    assert "active_view" in state


def test_empty_state_active_view_defaults_to_all():
    state = empty_state()
    assert state["active_view"] == "all"


# ---------------------------------------------------------------------------
# State path migration: tmux-web -> muxplex
# ---------------------------------------------------------------------------


def test_state_dir_uses_muxplex_name():
    """STATE_DIR default should use 'muxplex', not 'tmux-web'."""
    import muxplex.state as state_mod

    # The _default_state_dir (used when env var is not set) must contain 'muxplex'
    assert "muxplex" in str(state_mod._default_state_dir)
    assert "tmux-web" not in str(state_mod._default_state_dir)
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_state.py::test_empty_state_has_active_view_key muxplex/tests/test_state.py::test_empty_state_active_view_defaults_to_all muxplex/tests/test_state.py::test_state_dir_uses_muxplex_name -v
```
Expected: FAIL — `active_view` not in state, and `tmux-web` is still in `_default_state_dir`

**Step 3: Apply the changes to `muxplex/state.py`**

Change 1 — Update `_default_state_dir` (line 40):
```python
# Before:
_default_state_dir = Path.home() / ".local" / "share" / "tmux-web"

# After:
_default_state_dir = Path.home() / ".local" / "share" / "muxplex"
```

Change 2 — Keep env var name for backward compatibility but update the variable name:
```python
# Before:
STATE_DIR: Path = Path(os.environ.get("TMUX_WEB_STATE_DIR", _default_state_dir))

# After:
STATE_DIR: Path = Path(os.environ.get("MUXPLEX_STATE_DIR", os.environ.get("TMUX_WEB_STATE_DIR", _default_state_dir)))
```

Change 3 — Add `active_view` to `empty_state()` (line 60-66):
```python
def empty_state() -> dict:
    """Return a fresh, empty top-level state dict.

    Every call returns a fully independent object — no shared mutables.
    """
    return {
        "active_session": None,
        "active_remote_id": None,
        "active_view": "all",
        "session_order": [],
        "sessions": {},
        "devices": {},
    }
```

Change 4 — Update the module docstring to include `active_view` in the schema (top of file, add after `active_remote_id` in the schema comment):
```python
#    "active_view": str,  # "all" | "hidden" | view name
```

**Step 4: Run all state tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_state.py -v
```
Expected: All tests PASS (including the 3 new ones and all existing ones)

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/state.py muxplex/tests/test_state.py && git commit -m "feat: migrate state path to muxplex, add active_view field"
```

---

## Task 3: Update `settings.py` — Add `views` to Settings + Remove `filtered` from `gridViewMode`

**Files:**
- Modify: `muxplex/settings.py`
- Modify: `muxplex/tests/test_settings.py`

**Step 1: Write the failing tests**

Add to the end of `muxplex/tests/test_settings.py`:

```python
# ---------------------------------------------------------------------------
# views in DEFAULT_SETTINGS and SYNCABLE_KEYS
# ---------------------------------------------------------------------------


def test_views_in_default_settings():
    """DEFAULT_SETTINGS must include 'views' as an empty list."""
    assert "views" in DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["views"] == []


def test_views_in_syncable_keys():
    """'views' must be in SYNCABLE_KEYS so it syncs across federation."""
    assert "views" in SYNCABLE_KEYS


def test_views_roundtrip_through_save_and_load(tmp_path, monkeypatch):
    """Views data survives a save/load cycle."""
    fake_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_path)

    views_data = [
        {"name": "Work", "sessions": ["abc:dev-server", "def:monitoring"]},
        {"name": "Hobby", "sessions": ["abc:3d-printer"]},
    ]
    save_settings({"views": views_data})
    result = load_settings()
    assert result["views"] == views_data


def test_patch_settings_syncs_views(tmp_path, monkeypatch):
    """Patching 'views' bumps settings_updated_at (because views is in SYNCABLE_KEYS)."""
    fake_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_path)

    result = patch_settings({"views": [{"name": "Test", "sessions": []}]})
    assert result["views"] == [{"name": "Test", "sessions": []}]
    assert result["settings_updated_at"] > 0
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_settings.py::test_views_in_default_settings muxplex/tests/test_settings.py::test_views_in_syncable_keys -v
```
Expected: FAIL — `views` not in DEFAULT_SETTINGS or SYNCABLE_KEYS

**Step 3: Apply the changes to `muxplex/settings.py`**

Change 1 — Add `views` to `DEFAULT_SETTINGS` (after `"hidden_sessions": []` on line 24):
```python
    "hidden_sessions": [],
    "views": [],
```

Change 2 — Add `"views"` to `SYNCABLE_KEYS` (in the Session behavior section, after `"hidden_sessions"`):
```python
        "hidden_sessions",
        "views",
```

**Step 4: Run all settings tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_settings.py -v
```
Expected: All tests PASS

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/settings.py muxplex/tests/test_settings.py && git commit -m "feat: add views to DEFAULT_SETTINGS and SYNCABLE_KEYS"
```

---

## Task 4: Create `muxplex/views.py` — Mutual Exclusion Invariant Functions

**Files:**
- Create: `muxplex/views.py`
- Create: `muxplex/tests/test_views.py`

**Step 1: Write the failing tests**

Create `muxplex/tests/test_views.py`:

```python
"""
Tests for muxplex/views.py — views invariant enforcement.
"""

import pytest

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
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_views.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'muxplex.views'`

**Step 3: Write the implementation**

Create `muxplex/views.py`:

```python
"""
Views invariant enforcement and validation for muxplex.

Core invariants:
- hidden_sessions and any views[].sessions never share a session key.
- View names are non-empty, max 30 chars, trimmed, unique, not reserved.
- Duplicate session keys within a view are deduplicated.
"""

RESERVED_VIEW_NAMES = frozenset({"all", "hidden"})
MAX_VIEW_NAME_LENGTH = 30


def enforce_mutual_exclusion(settings: dict) -> dict:
    """Enforce that hidden_sessions and view sessions are disjoint.

    If a session key appears in both hidden_sessions and any view,
    it is removed from hidden_sessions (favor visibility over hiding).

    Also deduplicates session keys within each view.

    Mutates and returns the settings dict.
    """
    views = settings.get("views", [])
    hidden = settings.get("hidden_sessions", [])

    # Collect all session keys across all views
    all_view_sessions: set[str] = set()
    for view in views:
        all_view_sessions.update(view.get("sessions", []))

    # Remove overlap from hidden (favor visibility)
    if all_view_sessions and hidden:
        settings["hidden_sessions"] = [
            s for s in hidden if s not in all_view_sessions
        ]

    # Deduplicate session keys within each view (preserve order)
    for view in views:
        sessions = view.get("sessions", [])
        seen: set[str] = set()
        deduped: list[str] = []
        for s in sessions:
            if s not in seen:
                seen.add(s)
                deduped.append(s)
        view["sessions"] = deduped

    return settings


def validate_view_name(name: str, existing_views: list[dict]) -> str | None:
    """Validate a view name. Returns an error message string, or None if valid.

    Rules:
    - Non-empty after trimming
    - Max 30 characters after trimming
    - Not a reserved name ("all", "hidden") case-insensitive
    - Unique among existing views (case-sensitive match)
    """
    trimmed = name.strip()
    if not trimmed:
        return "View name cannot be empty"
    if len(trimmed) > MAX_VIEW_NAME_LENGTH:
        return f"View name must be {MAX_VIEW_NAME_LENGTH} characters or fewer"
    if trimmed.lower() in RESERVED_VIEW_NAMES:
        return f"'{trimmed}' is a reserved name"
    existing_names = {v.get("name", "") for v in existing_views}
    if trimmed in existing_names:
        return f"A view named '{trimmed}' already exists"
    return None
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_views.py -v
```
Expected: All 15 tests PASS

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/views.py muxplex/tests/test_views.py && git commit -m "feat: add views.py with mutual exclusion invariant and name validation"
```

---

## Task 5: Extend `/api/instance-info` to Return `device_id`

**Files:**
- Modify: `muxplex/main.py` (lines 866–880)
- Modify: `muxplex/tests/test_api.py`

**Step 1: Write the failing test**

Add to `muxplex/tests/test_api.py`, after the existing instance-info tests (after line ~1293):

```python
def test_instance_info_includes_device_id(client, tmp_path, monkeypatch):
    """GET /api/instance-info returns a device_id field."""
    import muxplex.identity as identity_mod
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(identity_mod, "IDENTITY_PATH", tmp_path / "identity.json")

    response = client.get("/api/instance-info")
    assert response.status_code == 200
    data = response.json()
    assert "device_id" in data, f"Response must include 'device_id', got: {data}"
    # Must be a non-empty string
    assert isinstance(data["device_id"], str) and len(data["device_id"]) > 0
```

**Step 2: Run test to verify it fails**

```bash
cd muxplex && python -m pytest muxplex/tests/test_api.py::test_instance_info_includes_device_id -v
```
Expected: FAIL — `device_id` not in response

**Step 3: Apply the change to `muxplex/main.py`**

Change 1 — Add import at the top of main.py (in the imports section, after the settings imports around line 67–73):
```python
from muxplex.identity import load_device_id
```

Change 2 — Modify the `instance_info()` function (line 866–880):
```python
@app.get("/api/instance-info")
async def instance_info() -> dict:
    """Return this instance's display name, version, and device identity.

    Public endpoint (no auth required) — used by remote instances to
    discover peer names, verify reachability, and obtain device_id.
    """
    settings = load_settings()
    # Read fresh so the UI reflects key-file changes without requiring a restart.
    fed_key = load_federation_key()
    return {
        "name": settings["device_name"],
        "device_id": load_device_id(),
        "version": app.version,
        "federation_enabled": bool(fed_key),
    }
```

**Step 4: Run the test to verify it passes**

```bash
cd muxplex && python -m pytest muxplex/tests/test_api.py::test_instance_info_includes_device_id -v
```
Expected: PASS

**Step 5: Run the full instance-info test suite to check for regressions**

```bash
cd muxplex && python -m pytest muxplex/tests/test_api.py -k "instance_info" -v
```
Expected: All instance-info tests PASS

**Step 6: Commit**

```bash
cd muxplex && git add muxplex/main.py muxplex/tests/test_api.py && git commit -m "feat: include device_id in /api/instance-info response"
```

---

## Task 6: Add `--reset-device-id` CLI Command

**Files:**
- Modify: `muxplex/cli.py`
- Modify: `muxplex/tests/test_cli.py`

**Step 1: Write the failing tests**

Add to `muxplex/tests/test_cli.py`:

```python
# ---------------------------------------------------------------------------
# reset-device-id subcommand tests
# ---------------------------------------------------------------------------


def test_reset_device_id_writes_new_id(tmp_path, monkeypatch, capsys):
    """reset_device_id_command() generates a new device_id and prints confirmation."""
    import json

    import muxplex.identity as identity_mod
    from muxplex.cli import reset_device_id_command

    fake_path = tmp_path / "identity.json"
    monkeypatch.setattr(identity_mod, "IDENTITY_PATH", fake_path)

    # Create an initial identity
    original_id = identity_mod.load_device_id()

    # Reset it
    reset_device_id_command()

    # File must have a different device_id
    data = json.loads(fake_path.read_text())
    assert data["device_id"] != original_id

    # Output must mention warning about orphaned keys
    captured = capsys.readouterr()
    assert "device_id" in captured.out.lower() or "identity" in captured.out.lower()
    assert "warning" in captured.out.lower() or "orphan" in captured.out.lower()


def test_main_dispatches_to_reset_device_id(monkeypatch):
    """main() with 'reset-device-id' subcommand must invoke reset_device_id_command()."""
    import muxplex.cli as cli_mod

    calls = []
    monkeypatch.setattr(cli_mod, "reset_device_id_command", lambda: calls.append(True))
    with patch("sys.argv", ["muxplex", "reset-device-id"]):
        cli_mod.main()
    assert calls, (
        "reset_device_id_command() must be called for 'reset-device-id' subcommand"
    )
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_cli.py::test_reset_device_id_writes_new_id muxplex/tests/test_cli.py::test_main_dispatches_to_reset_device_id -v
```
Expected: FAIL — `ImportError: cannot import name 'reset_device_id_command'`

**Step 3: Apply the changes to `muxplex/cli.py`**

Change 1 — Add the function (after the `reset_secret()` function, around line 150):
```python
def reset_device_id_command() -> None:
    """Regenerate the device identity UUID and warn about orphaned session keys."""
    from muxplex.identity import IDENTITY_PATH, load_device_id, reset_device_id

    old_id = None
    try:
        old_id = load_device_id()
    except Exception:
        pass

    new_id = reset_device_id()
    print(f"New device_id: {new_id}")
    print(f"Identity file: {IDENTITY_PATH}")
    if old_id:
        print(f"Previous device_id: {old_id}")
    print("Warning: session keys in views and hidden_sessions that referenced")
    print("the old device_id are now orphaned and will not match this instance.")
```

Change 2 — Register the subparser (after the `reset-secret` parser registration, around line 954):
```python
    sub.add_parser(
        "reset-device-id",
        help="Regenerate device identity UUID (orphans existing session keys)",
    )
```

Change 3 — Add dispatch (after the `reset-secret` dispatch, around line 1007):
```python
    elif args.command == "reset-device-id":
        reset_device_id_command()
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_cli.py::test_reset_device_id_writes_new_id muxplex/tests/test_cli.py::test_main_dispatches_to_reset_device_id -v
```
Expected: Both PASS

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/cli.py muxplex/tests/test_cli.py && git commit -m "feat: add --reset-device-id CLI command"
```

---

## Task 7: Wire Post-Sync Invariant Repair into Settings Sync

**Files:**
- Modify: `muxplex/settings.py`
- Modify: `muxplex/tests/test_settings.py`

**Step 1: Write the failing test**

Add to `muxplex/tests/test_settings.py`:

```python
def test_apply_synced_settings_enforces_mutual_exclusion(tmp_path, monkeypatch):
    """apply_synced_settings() runs mutual exclusion repair after applying synced data."""
    import json

    fake_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_path)

    # Pre-populate settings with a hidden session
    save_settings({"hidden_sessions": ["abc:dev"], "views": []})

    # Incoming sync adds a view containing the hidden session
    incoming = {
        "views": [{"name": "Work", "sessions": ["abc:dev"]}],
        "hidden_sessions": ["abc:dev"],
    }
    result = apply_synced_settings(incoming, 999.0)

    # Mutual exclusion: abc:dev should be removed from hidden_sessions
    assert "abc:dev" not in result["hidden_sessions"]
    assert "abc:dev" in result["views"][0]["sessions"]
```

**Step 2: Run test to verify it fails**

```bash
cd muxplex && python -m pytest muxplex/tests/test_settings.py::test_apply_synced_settings_enforces_mutual_exclusion -v
```
Expected: FAIL — `abc:dev` still in `hidden_sessions`

**Step 3: Apply the change to `muxplex/settings.py`**

Change the `apply_synced_settings` function (line 162–174) to call `enforce_mutual_exclusion` after applying synced keys:

```python
def apply_synced_settings(incoming_settings: dict, incoming_timestamp: float) -> dict:
    """Apply synced settings from a remote server.

    Only applies keys that are in SYNCABLE_KEYS. Sets settings_updated_at
    to the incoming timestamp (NOT time.time()) to prevent sync loops.
    Runs mutual exclusion invariant repair after applying.
    """
    from muxplex.views import enforce_mutual_exclusion

    current = load_settings()
    for key in SYNCABLE_KEYS:
        if key in incoming_settings:
            current[key] = incoming_settings[key]
    current["settings_updated_at"] = incoming_timestamp
    enforce_mutual_exclusion(current)
    save_settings(current)
    return current
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_settings.py -v
```
Expected: All tests PASS

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/settings.py muxplex/tests/test_settings.py && git commit -m "feat: run mutual exclusion invariant repair after settings sync"
```

---

## Task 8: Federation Proxy — Helper Function to Look Up Remote by `device_id`

**Files:**
- Modify: `muxplex/main.py`
- Modify: `muxplex/tests/test_api.py`

This task adds a shared helper function that looks up a remote instance by `device_id` instead of integer index. Tasks 9 and 10 will use this helper to rewrite the federation endpoints.

**Step 1: Write the failing test**

Add to `muxplex/tests/test_api.py`:

```python
# ---------------------------------------------------------------------------
# _lookup_remote_by_device_id helper
# ---------------------------------------------------------------------------


def test_lookup_remote_by_device_id_found(tmp_path, monkeypatch):
    """_lookup_remote_by_device_id returns the remote dict when device_id matches."""
    import json

    import muxplex.main as main_mod
    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {"url": "https://pi:8088", "name": "pi", "device_id": "aaa-111"},
                    {"url": "https://desktop:8088", "name": "desktop", "device_id": "bbb-222"},
                ]
            }
        )
    )

    remote = main_mod._lookup_remote_by_device_id("bbb-222")
    assert remote is not None
    assert remote["name"] == "desktop"


def test_lookup_remote_by_device_id_not_found(tmp_path, monkeypatch):
    """_lookup_remote_by_device_id returns None when no remote matches."""
    import json

    import muxplex.main as main_mod
    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {"url": "https://pi:8088", "name": "pi", "device_id": "aaa-111"},
                ]
            }
        )
    )

    remote = main_mod._lookup_remote_by_device_id("zzz-999")
    assert remote is None
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_api.py::test_lookup_remote_by_device_id_found muxplex/tests/test_api.py::test_lookup_remote_by_device_id_not_found -v
```
Expected: FAIL — `AttributeError: module 'muxplex.main' has no attribute '_lookup_remote_by_device_id'`

**Step 3: Add the helper function to `muxplex/main.py`**

Add this function before the federation proxy section (before the `federation_terminal_ws_proxy` function, around line 1008):

```python
def _lookup_remote_by_device_id(device_id: str) -> dict | None:
    """Look up a remote instance by device_id.

    Returns the remote dict from remote_instances if found, None otherwise.
    Supports both the new device_id-based lookup and falls back to integer
    index lookup during the transition period.
    """
    settings = load_settings()
    remotes = settings.get("remote_instances", [])

    # Primary: match by device_id field
    for remote in remotes:
        if remote.get("device_id") == device_id:
            return remote

    # Fallback: if device_id looks like an integer, try index-based lookup
    # (transition compatibility for old-format URLs)
    try:
        idx = int(device_id)
        if 0 <= idx < len(remotes):
            return remotes[idx]
    except (ValueError, TypeError):
        pass

    return None
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_api.py::test_lookup_remote_by_device_id_found muxplex/tests/test_api.py::test_lookup_remote_by_device_id_not_found -v
```
Expected: Both PASS

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/main.py muxplex/tests/test_api.py && git commit -m "feat: add _lookup_remote_by_device_id helper for federation proxy"
```

---

## Task 9: Federation Proxy Endpoints — Switch to `device_id` Lookup

**Files:**
- Modify: `muxplex/main.py` (federation_connect, federation_bell_clear, federation_create_session, federation_delete_session, federation_terminal_ws_proxy)
- Modify: `muxplex/tests/test_api.py`

This task changes all federation proxy URL patterns from `/api/federation/{remote_id:int}/...` to `/api/federation/{device_id}/...` and uses `_lookup_remote_by_device_id()` instead of array indexing. The integer fallback in the helper ensures backward compatibility during migration.

**Step 1: Write the failing test**

Add to `muxplex/tests/test_api.py`:

```python
def test_federation_connect_by_device_id(client, tmp_path, monkeypatch):
    """POST /api/federation/{device_id}/connect/{session} accepts device_id strings."""
    import json

    import httpx

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {
                        "url": "https://pi:8088",
                        "name": "pi",
                        "key": "test-key",
                        "device_id": "aaa-111-bbb",
                    }
                ]
            }
        )
    )

    # Mock the federation client to return a success response
    async def mock_post(url, **kwargs):
        resp = httpx.Response(200, json={"status": "connected"})
        return resp

    monkeypatch.setattr(
        client.app.state, "federation_client", type("MockClient", (), {"post": mock_post})()
    )

    response = client.post("/api/federation/aaa-111-bbb/connect/my-session")
    assert response.status_code == 200


def test_federation_connect_device_id_not_found(client, tmp_path, monkeypatch):
    """POST /api/federation/{device_id}/connect/{session} returns 404 for unknown device_id."""
    import json

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(json.dumps({"remote_instances": []}))

    response = client.post("/api/federation/nonexistent-device/connect/my-session")
    assert response.status_code == 404
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_api.py::test_federation_connect_by_device_id muxplex/tests/test_api.py::test_federation_connect_device_id_not_found -v
```
Expected: FAIL — the endpoint expects `int` type for `remote_id`, not a UUID string. The request will fail with a 422 validation error.

**Step 3: Apply the changes to `muxplex/main.py`**

For each of the four federation HTTP proxy endpoints, change:
1. The route path parameter from `{remote_id}` to `{device_id}`
2. The function parameter from `remote_id: int` to `device_id: str`
3. The lookup from `remotes[remote_id]` to `_lookup_remote_by_device_id(device_id)`

**federation_connect** (line ~1337):
```python
@app.post("/api/federation/{device_id}/connect/{session_name}")
async def federation_connect(
    device_id: str, session_name: str, request: Request
) -> dict:
    """Proxy a connect POST to a remote instance to spawn its ttyd.

    Looks up the remote by device_id in remote_instances settings,
    sends POST {remote_url}/api/sessions/{session_name}/connect with a
    Bearer auth header, and returns the remote's JSON response.

    Raises HTTP 404 if device_id is not found in remote_instances.
    """
    remote = _lookup_remote_by_device_id(device_id)
    if remote is None:
        raise HTTPException(
            status_code=404,
            detail=f"Remote instance '{device_id}' not found",
        )

    remote_url: str = remote.get("url", "").rstrip("/")
    remote_key: str = remote.get("key", "")
    url = f"{remote_url}/api/sessions/{session_name}/connect"

    http_client: httpx.AsyncClient = request.app.state.federation_client
    try:
        resp = await http_client.post(
            url,
            headers={"Authorization": f"Bearer {remote_key}"} if remote_key else {},
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Remote returned {exc.response.status_code}",
        )
    except Exception as exc:
        _log.warning("federation_connect: remote %s unreachable: %s", remote_url, exc)
        raise HTTPException(
            status_code=503,
            detail=f"Remote unreachable: {remote_url} ({type(exc).__name__}: {exc})",
        )
```

Apply the same pattern to:

**federation_bell_clear** (line ~1383): Change route to `"/api/federation/{device_id}/sessions/{session_name}/bell/clear"`, parameter to `device_id: str`, lookup to `_lookup_remote_by_device_id(device_id)` with the same None → 404 pattern.

**federation_create_session** (line ~1431): Change route to `"/api/federation/{device_id}/sessions"`, parameter to `device_id: str`, lookup to `_lookup_remote_by_device_id(device_id)` with the same None → 404 pattern.

**federation_delete_session** (line ~1479): Change route to `"/api/federation/{device_id}/sessions/{session_name}"`, parameter to `device_id: str`, lookup to `_lookup_remote_by_device_id(device_id)` with the same None → 404 pattern.

**federation_terminal_ws_proxy** (line ~1011): Change route to `"/federation/{device_id}/terminal/ws"`, parameter to `device_id: str`, and change the lookup logic from array indexing to:
```python
    # Look up remote instance by device_id
    remote = _lookup_remote_by_device_id(device_id)
    if remote is None:
        await websocket.close(code=4004)
        return
```
Remove the old `settings = load_settings()` / `remote_instances` / bounds-check block and replace with the above.

**Step 4: Run the new tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_api.py::test_federation_connect_by_device_id muxplex/tests/test_api.py::test_federation_connect_device_id_not_found -v
```
Expected: Both PASS

**Step 5: Run the full API test suite to check for regressions**

```bash
cd muxplex && python -m pytest muxplex/tests/test_api.py -v --timeout=60
```
Expected: All tests PASS. Existing federation tests that use integer indices will still work because `_lookup_remote_by_device_id` has an integer fallback.

**Step 6: Commit**

```bash
cd muxplex && git add muxplex/main.py muxplex/tests/test_api.py && git commit -m "feat: switch federation proxy endpoints from integer index to device_id lookup"
```

---

## Task 10: Update `fetch_remote()` to Tag Sessions with `device_id`

**Files:**
- Modify: `muxplex/main.py` (the `fetch_remote` inner function and `_federation_cache`, lines ~1185–1304)
- Modify: `muxplex/tests/test_api.py`

This task changes the `federation_sessions` endpoint to tag each remote session with `device_id` instead of integer `remoteId`, and to generate `sessionKey` as `device_id:name` instead of `remoteId:name`. Local sessions also get tagged with the local device_id.

**Step 1: Write the failing test**

Add to `muxplex/tests/test_api.py`:

```python
def test_federation_sessions_tags_local_with_device_id(client, tmp_path, monkeypatch):
    """GET /api/federation/sessions includes deviceId for local sessions."""
    import json

    import muxplex.identity as identity_mod
    import muxplex.main as main_mod
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(identity_mod, "IDENTITY_PATH", tmp_path / "identity.json")
    # Write identity file
    (tmp_path / "identity.json").write_text(json.dumps({"device_id": "local-uuid"}))

    # Mock get_session_list to return one session
    monkeypatch.setattr(main_mod, "get_session_list", lambda: ["dev"])
    monkeypatch.setattr(main_mod, "get_snapshots", lambda: {})

    response = client.get("/api/federation/sessions")
    assert response.status_code == 200
    data = response.json()
    local_sessions = [s for s in data if s.get("name") == "dev"]
    assert len(local_sessions) == 1
    assert local_sessions[0].get("deviceId") == "local-uuid"
    assert local_sessions[0].get("sessionKey") == "local-uuid:dev"
```

**Step 2: Run test to verify it fails**

```bash
cd muxplex && python -m pytest muxplex/tests/test_api.py::test_federation_sessions_tags_local_with_device_id -v
```
Expected: FAIL — local sessions don't have `deviceId` or the new `sessionKey` format

**Step 3: Apply the changes to `muxplex/main.py`**

Change 1 — In the `federation_sessions` function (line ~1192), add local device_id import and tagging:

```python
@app.get("/api/federation/sessions")
async def federation_sessions(request: Request) -> list[dict]:
    """Fetch sessions from all instances (local + remotes) and merge.

    Local sessions are tagged with deviceName, deviceId, and sessionKey.
    Remote sessions are fetched concurrently via asyncio.gather with Bearer auth
    headers. Failed remotes produce a status entry with status='unreachable' or
    status='auth_failed'.
    """
    settings = load_settings()
    local_device_name: str = settings.get("device_name", "")
    local_device_id: str = load_device_id()
    remote_instances: list[dict] = settings.get("remote_instances", [])

    # Build local sessions with deviceName/deviceId/sessionKey tags
    names = get_session_list()
    snapshots = get_snapshots()
    state = await read_state()
    local_sessions: list[dict] = []
    for name in names:
        session_state = state.get("sessions", {}).get(name, {})
        bell = session_state.get("bell", empty_bell())
        local_sessions.append(
            {
                "name": name,
                "snapshot": snapshots.get(name, ""),
                "bell": bell,
                "deviceName": local_device_name,
                "deviceId": local_device_id,
                "remoteId": None,
                "sessionKey": f"{local_device_id}:{name}",
            }
        )
```

Change 2 — In the `fetch_remote` inner function, use `device_id` from the remote instance for tagging instead of the integer index. Change the `remote_id: int = i` line and all references:

```python
    async def fetch_remote(i: int, remote: dict) -> list[dict]:
        url: str = remote.get("url", "")
        key: str = remote.get("key", "")
        remote_name: str = remote.get("name", url)
        remote_device_id: str = remote.get("device_id", str(i))
        # ... rest of function uses remote_device_id instead of remote_id
```

In the tagged session list comprehension:
```python
            tagged = [
                {
                    **s,
                    "deviceName": remote_name,
                    "deviceId": remote_device_id,
                    "remoteId": remote_device_id,
                    "sessionKey": f"{remote_device_id}:{s.get('name', '')}",
                }
                for s in sessions
            ]
```

In all status entries (`auth_failed`, `empty`, `unreachable`), change `"remoteId": remote_id` to `"remoteId": remote_device_id` and add `"deviceId": remote_device_id`.

Change 3 — Update `_federation_cache` type hint from `dict[int, dict]` to `dict[str, dict]` (line ~1188):
```python
_federation_cache: dict[str, dict] = {}
```

And change all cache key references from `remote_id` to `remote_device_id`.

**Step 4: Run the new test to verify it passes**

```bash
cd muxplex && python -m pytest muxplex/tests/test_api.py::test_federation_sessions_tags_local_with_device_id -v
```
Expected: PASS

**Step 5: Run the full API test suite**

```bash
cd muxplex && python -m pytest muxplex/tests/test_api.py -v --timeout=60
```
Expected: All tests PASS. Existing tests that check `remoteId` will still pass because we kept the `remoteId` field (now containing device_id string instead of integer, but the existing tests that create mock remotes will work with the string fallback).

> **Note to implementer:** Some existing federation tests may need minor adjustments if they assert `remoteId` is an integer. If a test fails, check if it's asserting `remoteId == 0` (integer) — change the assertion to match the new `device_id` string. The `_federation_cache` key type change may also require updating existing test expectations.

**Step 6: Commit**

```bash
cd muxplex && git add muxplex/main.py muxplex/tests/test_api.py && git commit -m "feat: tag sessions with device_id-based sessionKey in federation_sessions"
```

---

## Task 11: Frontend — Change Session Key Format to `device_id:name`

**Files:**
- Modify: `muxplex/frontend/app.js`
- Modify: `muxplex/tests/test_frontend_js.py`

The backend now sends `deviceId` and `sessionKey` in `device_id:name` format on every session object. The frontend needs to:
1. Use `session.deviceId` instead of `session.remoteId` for federation API calls
2. Use `session.sessionKey` (already present from the backend) instead of constructing keys locally
3. Update `data-remote-id` attributes to use `deviceId`
4. Update `createNewSession()` and `killSession()` to use `deviceId` in API URLs
5. Update `openSession()` to accept `deviceId` instead of `remoteId`
6. Update state patches to use `deviceId` for `active_remote_id`

**Step 1: Write the failing test**

Add to `muxplex/tests/test_frontend_js.py` (find the section for session key tests or add at the end):

```python
def test_session_tile_uses_device_id_in_data_attribute(page):
    """Session tiles use deviceId in data-remote-id attribute."""
    page.evaluate("""() => {
        const app = window.MuxplexApp;
        app._setServerSettings({
            hidden_sessions: [],
            sort_order: 'manual',
        });
        app._setCurrentSessions([
            { name: 'dev', deviceName: 'pi', deviceId: 'abc-123', remoteId: 'abc-123', sessionKey: 'abc-123:dev', bell: { unseen_count: 0 } },
        ]);
    }""")
    # Render the grid
    page.evaluate("() => window.MuxplexApp.renderGrid()")
    tile = page.query_selector('[data-session="dev"]')
    assert tile is not None
    remote_id_attr = tile.get_attribute("data-remote-id")
    assert remote_id_attr == "abc-123"
```

> **Note to implementer:** The frontend JS test file uses a specific test framework. Look at the top of `muxplex/tests/test_frontend_js.py` to understand the exact test setup pattern (it may use `subprocess` to run Node tests, or pytest with a browser fixture). Match whatever pattern exists. If the test file uses Node.js + jsdom or a similar approach, adapt the test accordingly. The assertion content is what matters — verify `data-remote-id` uses the `deviceId` string.

**Step 2: Run the test to verify it fails**

Run the existing frontend JS test suite first to understand the baseline:
```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py -k "device_id" -v
```

**Step 3: Apply the changes to `muxplex/frontend/app.js`**

This is a large set of find-and-replace changes. The key transformations:

Change 1 — In `createNewSession()` (line ~2089–2092), replace remoteId with deviceId in the API URL construction:
```javascript
// Before:
  remoteId = remoteId || '';
  try {
    var endpoint = remoteId ? '/api/federation/' + encodeURIComponent(remoteId) + '/sessions' : '/api/sessions';

// After:
  var deviceId = remoteId || '';  // Accept either name during transition
  try {
    var endpoint = deviceId ? '/api/federation/' + encodeURIComponent(deviceId) + '/sessions' : '/api/sessions';
```

Change 2 — In `openSession()` (line ~1228–1232), use deviceId for the federation connect URL:
```javascript
// Before:
  var _remoteId = opts.remoteId != null ? opts.remoteId : '';
  try {
    if (_remoteId !== '') {

// After:
  var _deviceId = opts.remoteId != null ? opts.remoteId : '';
  try {
    if (_deviceId !== '') {
```
And update the `/api/federation/` URL to use `_deviceId`.

Change 3 — In `killSession()` function, update the federation delete URL to use `deviceId`:
Find the federation delete endpoint URL construction and change `remoteId` to `deviceId` from the session data.

Change 4 — In `getVisibleSessions()` (line ~537–547), update hidden session matching to use `sessionKey` instead of `name`:
```javascript
// Before:
    if (hidden.length > 0 && hidden.includes(s.name)) {

// After:
    if (hidden.length > 0 && (hidden.includes(s.sessionKey || s.name) || hidden.includes(s.name))) {
```
This provides backward compatibility — `hidden_sessions` may contain either old format (plain name) or new format (`device_id:name`).

Change 5 — In the `_viewingRemoteId` state variable and its usage, rename conceptually to represent device_id. Since this is used throughout the file, the simplest approach is to keep the variable name `_viewingRemoteId` but ensure it stores the `deviceId` value. The `data-remote-id` attributes already work because the backend now sends `deviceId` as the `remoteId` value.

Change 6 — In the `PATCH /api/state` call that sets `active_remote_id`, ensure it sends the device_id string:
```javascript
// The value stored is already opts.remoteId which now contains deviceId
// No code change needed if the backend accepts strings for active_remote_id
```

> **Important:** The backend's `StatePatch` model has `active_remote_id: str | None` (line 428 of main.py), so it already accepts strings. No backend change needed here.

**Step 4: Run the frontend tests**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py -v --timeout=120
```
Expected: All tests PASS. The `deviceId` field is backward-compatible because the backend now sends it alongside `remoteId`.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/tests/test_frontend_js.py && git commit -m "feat: update frontend to use device_id-based session keys and API URLs"
```

---

## Task 12: Remove `filtered` from `gridViewMode` Options

**Files:**
- Modify: `muxplex/frontend/app.js`
- Modify: `muxplex/tests/test_frontend_js.py`

The design removes `filtered` as a `gridViewMode` value. Only `flat` and `grouped` remain.

**Step 1: Write the failing test**

Add to `muxplex/tests/test_frontend_js.py`:

```python
def test_grid_view_mode_filtered_not_available(page):
    """gridViewMode 'filtered' should not be recognized — defaults to 'flat'."""
    page.evaluate("""() => {
        const app = window.MuxplexApp;
        app._setGridViewMode('filtered');
    }""")
    mode = page.evaluate("() => window.MuxplexApp._getGridViewMode()")
    # After removing filtered, setting it should fall back to 'flat'
    assert mode == "flat"
```

**Step 2: Run test to verify it fails**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py -k "filtered_not_available" -v
```
Expected: FAIL — `_setGridViewMode('filtered')` currently accepts the value

**Step 3: Apply the changes to `muxplex/frontend/app.js`**

Change 1 — Remove all `filtered`-specific code paths in `renderGrid()` (lines ~761-763, ~780-784, ~822-823):

Remove:
```javascript
  // In filtered mode, apply device filter
  if (_gridViewMode === 'filtered' && _activeFilterDevice !== 'all') {
    visible = visible.filter(function(s) { return s.deviceName === _activeFilterDevice; });
  }
```

Remove all `if (_gridViewMode === 'filtered')` blocks that render the filter bar.

Change 2 — In `loadGridViewMode()` (line ~1547), add a guard:
```javascript
function loadGridViewMode() {
  var ds = getDisplaySettings();
  var mode = ds.gridViewMode || 'flat';
  // 'filtered' was removed in the Views feature — fall back to 'flat'
  if (mode === 'filtered') mode = 'flat';
  return mode;
}
```

Change 3 — In `_setGridViewMode()` test helper, add the same guard:
```javascript
function _setGridViewMode(mode) {
  if (mode === 'filtered') mode = 'flat';
  _gridViewMode = mode;
}
```

Change 4 — In `DISPLAY_DEFAULTS` (line ~149), the comment already says `'flat' | 'grouped'`. No change needed there.

Change 5 — Remove the `_activeFilterDevice` state variable and `renderFilterBar` function, or leave them as dead code for now (they'll be removed when the filter bar HTML is removed in Phase 2). The safer approach is to leave them and let Phase 2 clean up the HTML. Just ensure `_gridViewMode` never gets set to `'filtered'`.

**Step 4: Run the frontend tests**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py -v --timeout=120
```
Expected: All tests PASS. Tests that explicitly test `filtered` mode should be updated to expect `flat` fallback behavior.

> **Note to implementer:** If existing tests assert that setting gridViewMode to 'filtered' works, update those tests to expect 'flat' instead. Search for `'filtered'` in `test_frontend_js.py`.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/tests/test_frontend_js.py && git commit -m "feat: remove 'filtered' gridViewMode, keep only flat and grouped"
```

---

## Task 13: Final Integration — Run Full Test Suite

**Files:** None (verification only)

**Step 1: Run the complete test suite**

```bash
cd muxplex && python -m pytest muxplex/tests/ -v --timeout=120
```
Expected: All tests PASS

**Step 2: Run quality checks**

```bash
cd muxplex && python -m ruff check muxplex/
cd muxplex && python -m ruff format --check muxplex/
```
Expected: No errors. Fix any formatting or lint issues.

**Step 3: Verify the data flow end-to-end**

Check that the full chain works by listing what Phase 1 established:

1. `identity.py` creates/loads device_id from `~/.config/muxplex/identity.json` ✓
2. `state.py` uses `~/.local/share/muxplex/` and includes `active_view` ✓
3. `settings.py` includes `views` in DEFAULT_SETTINGS and SYNCABLE_KEYS ✓
4. `views.py` enforces mutual exclusion between hidden and view sessions ✓
5. `/api/instance-info` returns `device_id` ✓
6. `--reset-device-id` CLI command works ✓
7. Post-sync invariant repair runs after `apply_synced_settings()` ✓
8. Federation proxy endpoints accept `device_id` strings ✓
9. `federation_sessions` tags all sessions with `device_id:name` keys ✓
10. Frontend uses `deviceId` for API calls and session keys ✓
11. `filtered` gridViewMode removed ✓

**Step 4: Commit any remaining fixes**

```bash
cd muxplex && git add -A && git status
```
If there are uncommitted changes, commit them:
```bash
cd muxplex && git commit -m "chore: phase 1 integration fixes"
```

---

## Deferred to Phase 2

The following are explicitly NOT in Phase 1:

- **Session key migration logic** — rewriting old positional `remoteId:name` keys in `hidden_sessions` and `session_order` to `device_id:name`. The integer fallback in `_lookup_remote_by_device_id()` provides backward compatibility, so migration can happen in Phase 2 when the remote's `device_id` is first discovered via `/api/instance-info`.
- **Header dropdown UI** for view switching
- **Tile flyout menu** (`⋮` button)
- **Add Sessions panel**
- **`getVisibleSessions()` rewrite** to filter by active view
- **Settings dialog** — Manage Views tab
- **Mobile variants** — bottom sheets
- **Config path migration** (moving files from `~/.local/share/tmux-web/` to `~/.local/share/muxplex/`) — the `MUXPLEX_STATE_DIR` env var and the fallback to `TMUX_WEB_STATE_DIR` provide compatibility. Actual file migration (copying old state.json to new location) can be added as a startup step in Phase 2 once the identity system is stable.
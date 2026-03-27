# Web-Tmux Dashboard — Phase 1: Backend Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Build the Python coordinator backend — session enumeration, state management, ttyd lifecycle, bell detection, and all JSON API endpoints — fully tested, with no frontend code.

**Architecture:** A FastAPI application runs a 2-second background poll loop that enumerates tmux sessions via `tmux list-sessions`, captures pane output via `capture-pane`, checks bell flags, and atomically writes state to `~/.local/share/tmux-web/state.json`. A single ttyd process (one session at a time, port 7682) is spawned and killed via `asyncio.create_subprocess_exec`. All concurrent state mutations serialize through an `asyncio.Lock`.

**Tech Stack:** Python 3.12, FastAPI 0.115+, uvicorn, pytest 8+, pytest-asyncio 0.23+, httpx (for TestClient), standard library only beyond that.

---

## File Map

All files live under `/home/bkrabach/dev/web-tmux/`. Create every directory and file listed — none exist yet.

```
/home/bkrabach/dev/web-tmux/
├── coordinator/
│   ├── __init__.py           ← empty
│   ├── main.py               ← FastAPI app, lifespan, all endpoints
│   ├── sessions.py           ← tmux enumeration + capture-pane
│   ├── state.py              ← state.json schema, atomic read/write, device management
│   ├── ttyd.py               ← ttyd spawn, kill, orphan detection
│   ├── bells.py              ← bell polling, unseen_count, clear rule
│   └── tests/
│       ├── __init__.py       ← empty
│       ├── test_sessions.py
│       ├── test_state.py
│       ├── test_ttyd.py
│       ├── test_bells.py
│       ├── test_api.py
│       └── test_integration.py
├── requirements.txt
└── pyproject.toml
```

---

## Task 0: Bell Flag Spike (Empirical — No TDD)

**Purpose:** Determine whether `tmux display-message -t {session} -p "#{window_bell_flag}"` clears the bell flag when read, or merely reads it. The bell implementation in Task 7 depends on this finding.

**Files:**
- Create: `coordinator/spike_bell_flag.py`

**Step 1: Create the spike script**

Create `/home/bkrabach/dev/web-tmux/coordinator/spike_bell_flag.py` with this exact content:

```python
#!/usr/bin/env python3
"""
Bell Flag Spike — run ONCE manually to determine tmux bell flag read behavior.

Usage:
    python3 coordinator/spike_bell_flag.py

What this tests:
    Does `tmux display-message -p "#{window_bell_flag}"` clear the flag when
    read, or does the flag persist until the window is visited in tmux?

Expected result (almost certain): the flag persists. Reading it does NOT clear
it. The flag is cleared only when the window is marked active inside tmux.
"""
import subprocess
import time

SESSION = "bell-spike-test"

def run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout.strip()

def main() -> None:
    # 1. Create a test session
    print("Creating test session...")
    subprocess.run(["tmux", "new-session", "-d", "-s", SESSION, "-x", "80", "-y", "24"],
                   check=True)
    time.sleep(0.2)

    # 2. Send a bell to the session
    print("Sending bell to session...")
    subprocess.run(["tmux", "send-keys", "-t", SESSION, "printf '\\a'", "Enter"],
                   check=True)
    time.sleep(0.5)

    # 3. Read the bell flag (first read)
    flag_read1 = run(["tmux", "display-message", "-t", SESSION, "-p",
                       "#{window_bell_flag}"])
    print(f"Bell flag (1st read): '{flag_read1}'")

    # 4. Read the bell flag immediately again (second read)
    flag_read2 = run(["tmux", "display-message", "-t", SESSION, "-p",
                       "#{window_bell_flag}"])
    print(f"Bell flag (2nd read): '{flag_read2}'")

    # 5. Cleanup
    subprocess.run(["tmux", "kill-session", "-t", SESSION])

    # 6. Report
    print()
    if flag_read1 == "1" and flag_read2 == "1":
        print("FINDING: Reading does NOT clear the flag. Both reads show '1'.")
        print("Implementation: use in-memory _bell_seen dict to detect 0→1 transitions.")
    elif flag_read1 == "1" and flag_read2 == "0":
        print("FINDING: Reading CLEARS the flag. First read shows '1', second shows '0'.")
        print("Implementation: each '1' is a new bell — no transition tracking needed.")
    elif flag_read1 == "0":
        print("WARNING: Bell flag not set after printf '\\a'. Try running manually:")
        print(f"  tmux send-keys -t {SESSION} \"printf '\\\\a'\" Enter")
        print("  Then check: tmux display-message -t bell-spike-test -p '#{window_bell_flag}'")
    else:
        print(f"UNEXPECTED: read1={flag_read1!r}, read2={flag_read2!r}")

if __name__ == "__main__":
    main()
```

**Step 2: Run the spike**

```bash
cd /home/bkrabach/dev/web-tmux
python3 coordinator/spike_bell_flag.py
```

Expected output:
```
Creating test session...
Sending bell to session...
Bell flag (1st read): '1'
Bell flag (2nd read): '1'

FINDING: Reading does NOT clear the flag. Both reads show '1'.
Implementation: use in-memory _bell_seen dict to detect 0→1 transitions.
```

**Step 3: Record findings**

The spike result tells us which branch to take in `bells.py` (Task 7). When you reach Task 7, you will put a comment at the top of `coordinator/bells.py` recording what the spike found. The plan assumes the almost-certain result: **reading does NOT clear the flag**.

**Step 4: Commit the spike script**

```bash
cd /home/bkrabach/dev/web-tmux
git add coordinator/spike_bell_flag.py
git commit -m "spike: bell flag read-behavior investigation script"
```

---

## Task 1: Project Setup

**Files:**
- Create: `coordinator/__init__.py`
- Create: `coordinator/tests/__init__.py`
- Create: `requirements.txt`
- Create: `pyproject.toml`

**Step 1: Create directory skeleton**

```bash
mkdir -p /home/bkrabach/dev/web-tmux/coordinator/tests
touch /home/bkrabach/dev/web-tmux/coordinator/__init__.py
touch /home/bkrabach/dev/web-tmux/coordinator/tests/__init__.py
```

**Step 2: Create `requirements.txt`**

Create `/home/bkrabach/dev/web-tmux/requirements.txt`:

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
httpx>=0.27.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

**Step 3: Create `pyproject.toml`**

Create `/home/bkrabach/dev/web-tmux/pyproject.toml`:

```toml
[project]
name = "tmux-web-coordinator"
version = "0.1.0"
description = "Web dashboard coordinator for tmux sessions"
requires-python = ">=3.12"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["coordinator"]

[tool.pytest.ini_options]
testpaths = ["coordinator/tests"]
asyncio_mode = "auto"
addopts = "--import-mode=importlib"
```

**Step 4: Install dependencies**

```bash
cd /home/bkrabach/dev/web-tmux
pip install fastapi "uvicorn[standard]" httpx pytest pytest-asyncio
```

Expected output: `Successfully installed fastapi-... uvicorn-... httpx-... pytest-... pytest-asyncio-...`

**Step 5: Verify pytest finds the test directory**

```bash
cd /home/bkrabach/dev/web-tmux
pytest --collect-only
```

Expected output:
```
======================== no tests ran ========================
```
(No errors — just no tests yet.)

**Step 6: Commit**

```bash
cd /home/bkrabach/dev/web-tmux
git add coordinator/__init__.py coordinator/tests/__init__.py requirements.txt pyproject.toml
git commit -m "feat: project setup — pyproject.toml, requirements, directory skeleton"
```

---

## Task 2: state.py — State Schema + Empty Factories

**Files:**
- Create: `coordinator/state.py`
- Create: `coordinator/tests/test_state.py`

**Step 1: Write the failing tests**

Create `/home/bkrabach/dev/web-tmux/coordinator/tests/test_state.py`:

```python
"""Tests for coordinator/state.py — schema and empty factory functions."""
import pytest


def test_empty_state_has_required_top_level_keys():
    from coordinator.state import empty_state

    s = empty_state()
    assert "active_session" in s
    assert "session_order" in s
    assert "sessions" in s
    assert "devices" in s


def test_empty_state_active_session_is_none():
    from coordinator.state import empty_state

    s = empty_state()
    assert s["active_session"] is None


def test_empty_state_session_order_is_empty_list():
    from coordinator.state import empty_state

    s = empty_state()
    assert s["session_order"] == []


def test_empty_state_sessions_is_empty_dict():
    from coordinator.state import empty_state

    s = empty_state()
    assert s["sessions"] == {}


def test_empty_state_devices_is_empty_dict():
    from coordinator.state import empty_state

    s = empty_state()
    assert s["devices"] == {}


def test_empty_bell_has_required_keys():
    from coordinator.state import empty_bell

    b = empty_bell()
    assert "last_fired_at" in b
    assert "seen_at" in b
    assert "unseen_count" in b


def test_empty_bell_last_fired_at_is_none():
    from coordinator.state import empty_bell

    b = empty_bell()
    assert b["last_fired_at"] is None


def test_empty_bell_seen_at_is_none():
    from coordinator.state import empty_bell

    b = empty_bell()
    assert b["seen_at"] is None


def test_empty_bell_unseen_count_is_zero():
    from coordinator.state import empty_bell

    b = empty_bell()
    assert b["unseen_count"] == 0


def test_empty_device_has_required_keys():
    from coordinator.state import empty_device

    d = empty_device("d-abc123", "Test Device")
    assert "label" in d
    assert "viewing_session" in d
    assert "view_mode" in d
    assert "last_interaction_at" in d
    assert "last_heartbeat_at" in d


def test_empty_device_label_set_correctly():
    from coordinator.state import empty_device

    d = empty_device("d-abc123", "Laptop Chrome")
    assert d["label"] == "Laptop Chrome"


def test_empty_device_viewing_session_is_none():
    from coordinator.state import empty_device

    d = empty_device("d-abc123", "Test")
    assert d["viewing_session"] is None


def test_empty_device_view_mode_is_grid():
    from coordinator.state import empty_device

    d = empty_device("d-abc123", "Test")
    assert d["view_mode"] == "grid"


def test_empty_state_returns_independent_dicts():
    """Two calls to empty_state() must not share mutable objects."""
    from coordinator.state import empty_state

    s1 = empty_state()
    s2 = empty_state()
    s1["session_order"].append("main")
    assert s2["session_order"] == [], "empty_state() must not share list objects"
```

**Step 2: Run tests to verify they fail**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_state.py -v
```

Expected: `ERROR` collecting tests — `ModuleNotFoundError: No module named 'coordinator.state'`

**Step 3: Create `coordinator/state.py` with schema and factories**

Create `/home/bkrabach/dev/web-tmux/coordinator/state.py`:

```python
"""
State management for the tmux-web coordinator.

State file location: ~/.local/share/tmux-web/state.json
(Override with TMUX_WEB_STATE_DIR env var for testing.)

State schema:
{
  "active_session": str | None,
  "session_order": list[str],
  "sessions": {
    "<name>": {
      "bell": {
        "last_fired_at": float | None,
        "seen_at": float | None,
        "unseen_count": int
      }
    }
  },
  "devices": {
    "<device_id>": {
      "label": str,
      "viewing_session": str | None,
      "view_mode": str,           # "fullscreen" | "grid"
      "last_interaction_at": float,
      "last_heartbeat_at": float
    }
  }
}

Concurrency model:
  - `state_lock`: asyncio.Lock — acquire before any read-modify-write cycle.
  - `read_state()` / `write_state()`: acquire the lock internally; safe for
    simple reads or writes that don't need to be atomic with each other.
  - `load_state()` / `save_state()`: NO lock; use inside `async with state_lock`.

Atomic write pattern (os.replace — atomic on POSIX):
  tmp = STATE_PATH.with_suffix('.tmp')
  tmp.write_text(json.dumps(state, indent=2))
  os.replace(tmp, STATE_PATH)
"""

import asyncio
import json
import os
import pathlib
import time

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_state_dir_override = os.environ.get("TMUX_WEB_STATE_DIR")
STATE_DIR: pathlib.Path = (
    pathlib.Path(_state_dir_override)
    if _state_dir_override
    else pathlib.Path.home() / ".local" / "share" / "tmux-web"
)
STATE_PATH: pathlib.Path = STATE_DIR / "state.json"

# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

state_lock: asyncio.Lock = asyncio.Lock()

# ---------------------------------------------------------------------------
# Schema factories
# ---------------------------------------------------------------------------


def empty_state() -> dict:
    """Return a fresh, empty state dict. Each call returns independent objects."""
    return {
        "active_session": None,
        "session_order": [],
        "sessions": {},
        "devices": {},
    }


def empty_bell() -> dict:
    """Return a fresh bell sub-dict for one session."""
    return {
        "last_fired_at": None,
        "seen_at": None,
        "unseen_count": 0,
    }


def empty_device(device_id: str, label: str) -> dict:
    """Return a fresh device sub-dict with defaults."""
    now = time.time()
    return {
        "label": label,
        "viewing_session": None,
        "view_mode": "grid",
        "last_interaction_at": now,
        "last_heartbeat_at": now,
    }
```

**Step 4: Run tests to verify they pass**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_state.py -v
```

Expected:
```
PASSED coordinator/tests/test_state.py::test_empty_state_has_required_top_level_keys
PASSED coordinator/tests/test_state.py::test_empty_state_active_session_is_none
... (all 15 tests) ...
15 passed in 0.XXs
```

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux
git add coordinator/state.py coordinator/tests/test_state.py
git commit -m "feat: state.py — schema factories and empty_state/bell/device"
```

---

## Task 3: state.py — Atomic Read/Write

**Files:**
- Modify: `coordinator/state.py`
- Modify: `coordinator/tests/test_state.py`

**Step 1: Add failing tests**

Append this to `/home/bkrabach/dev/web-tmux/coordinator/tests/test_state.py`:

```python
# ---------------------------------------------------------------------------
# Atomic read/write tests — require tmp_path fixture
# ---------------------------------------------------------------------------

import asyncio
import json
import pathlib


@pytest.fixture(autouse=True)
def use_tmp_state_dir(tmp_path, monkeypatch):
    """Redirect all state file operations to a temp directory."""
    import coordinator.state as state_mod

    monkeypatch.setattr(state_mod, "STATE_DIR", tmp_path)
    monkeypatch.setattr(state_mod, "STATE_PATH", tmp_path / "state.json")


async def test_read_state_returns_empty_when_no_file():
    from coordinator.state import read_state

    result = await read_state()
    assert result["active_session"] is None
    assert result["session_order"] == []


async def test_write_then_read_roundtrip(tmp_path):
    from coordinator.state import read_state, write_state

    original = {
        "active_session": "work",
        "session_order": ["main", "work"],
        "sessions": {},
        "devices": {},
    }
    await write_state(original)
    result = await read_state()
    assert result["active_session"] == "work"
    assert result["session_order"] == ["main", "work"]


async def test_write_creates_state_dir_if_missing(tmp_path, monkeypatch):
    import coordinator.state as state_mod

    deep_dir = tmp_path / "a" / "b" / "c"
    monkeypatch.setattr(state_mod, "STATE_DIR", deep_dir)
    monkeypatch.setattr(state_mod, "STATE_PATH", deep_dir / "state.json")
    await state_mod.write_state(state_mod.empty_state())
    assert (deep_dir / "state.json").exists()


async def test_write_is_atomic_no_tmp_file_left(tmp_path):
    from coordinator.state import STATE_PATH, write_state

    await write_state({"active_session": None, "session_order": [],
                        "sessions": {}, "devices": {}})
    tmp = STATE_PATH.with_suffix(".tmp")
    assert not tmp.exists(), ".tmp file must be cleaned up by os.replace()"


async def test_concurrent_writes_do_not_corrupt(tmp_path):
    """Two coroutines writing different states concurrently must not corrupt."""
    from coordinator.state import read_state, write_state

    state_a = {"active_session": "aaa", "session_order": ["aaa"],
                "sessions": {}, "devices": {}}
    state_b = {"active_session": "bbb", "session_order": ["bbb"],
                "sessions": {}, "devices": {}}

    async def write_a() -> None:
        for _ in range(5):
            await write_state(state_a)

    async def write_b() -> None:
        for _ in range(5):
            await write_state(state_b)

    await asyncio.gather(write_a(), write_b())

    # Final state must be valid JSON that is one of the two known states
    result = await read_state()
    assert result["active_session"] in ("aaa", "bbb")
```

**Step 2: Run tests to verify failures**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_state.py::test_read_state_returns_empty_when_no_file -v
```

Expected: `FAILED` — `ImportError: cannot import name 'read_state' from 'coordinator.state'`

**Step 3: Add `load_state`, `save_state`, `read_state`, `write_state` to `coordinator/state.py`**

Append this to the bottom of `/home/bkrabach/dev/web-tmux/coordinator/state.py` (after the `empty_device` function):

```python
# ---------------------------------------------------------------------------
# Low-level file I/O (no lock — use inside async with state_lock)
# ---------------------------------------------------------------------------


def load_state() -> dict:
    """Read state.json synchronously. Returns empty_state() if file missing."""
    if not STATE_PATH.exists():
        return empty_state()
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return empty_state()


def save_state(state: dict) -> None:
    """Write state.json atomically (os.replace). STATE_DIR must exist."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, STATE_PATH)


# ---------------------------------------------------------------------------
# Locked public API (safe to call from any async context)
# ---------------------------------------------------------------------------


async def read_state() -> dict:
    """Acquire state_lock, read state.json, return dict."""
    async with state_lock:
        return load_state()


async def write_state(state: dict) -> None:
    """Acquire state_lock, write state.json atomically."""
    async with state_lock:
        save_state(state)
```

**Step 4: Run tests to verify they pass**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_state.py -v
```

Expected:
```
PASSED ...test_read_state_returns_empty_when_no_file
PASSED ...test_write_then_read_roundtrip
PASSED ...test_write_creates_state_dir_if_missing
PASSED ...test_write_is_atomic_no_tmp_file_left
PASSED ...test_concurrent_writes_do_not_corrupt
... (all tests) ...
20 passed in 0.XXs
```

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux
git add coordinator/state.py coordinator/tests/test_state.py
git commit -m "feat: state.py — atomic read/write with asyncio lock"
```

---

## Task 4: state.py — Device Registration, Heartbeat, Pruning

**Files:**
- Modify: `coordinator/state.py`
- Modify: `coordinator/tests/test_state.py`

**Step 1: Add failing tests**

Append this to `/home/bkrabach/dev/web-tmux/coordinator/tests/test_state.py`:

```python
# ---------------------------------------------------------------------------
# Device registration and pruning tests
# ---------------------------------------------------------------------------


def test_register_device_adds_new_device():
    from coordinator.state import empty_state, register_device

    s = empty_state()
    register_device(s, "d-abc", "Laptop", viewing_session=None,
                    view_mode="grid", last_interaction_at=1000.0)
    assert "d-abc" in s["devices"]
    assert s["devices"]["d-abc"]["label"] == "Laptop"


def test_register_device_updates_existing_device():
    from coordinator.state import empty_state, register_device

    s = empty_state()
    register_device(s, "d-abc", "Laptop", viewing_session=None,
                    view_mode="grid", last_interaction_at=1000.0)
    register_device(s, "d-abc", "Laptop", viewing_session="work",
                    view_mode="fullscreen", last_interaction_at=2000.0)
    assert s["devices"]["d-abc"]["viewing_session"] == "work"
    assert s["devices"]["d-abc"]["view_mode"] == "fullscreen"
    assert s["devices"]["d-abc"]["last_interaction_at"] == 2000.0


def test_register_device_sets_heartbeat_timestamp():
    import time
    from coordinator.state import empty_state, register_device

    before = time.time()
    s = empty_state()
    register_device(s, "d-abc", "Laptop", viewing_session=None,
                    view_mode="grid", last_interaction_at=1000.0)
    after = time.time()
    hb = s["devices"]["d-abc"]["last_heartbeat_at"]
    assert before <= hb <= after


def test_prune_devices_removes_stale():
    from coordinator.state import empty_state, prune_devices

    s = empty_state()
    s["devices"]["old"] = {
        "label": "Old Device",
        "viewing_session": None,
        "view_mode": "grid",
        "last_interaction_at": 0.0,
        "last_heartbeat_at": 0.0,      # very old — epoch
    }
    removed = prune_devices(s, ttl_seconds=300.0)
    assert "old" not in s["devices"]
    assert "old" in removed


def test_prune_devices_keeps_fresh():
    import time
    from coordinator.state import empty_state, prune_devices

    s = empty_state()
    now = time.time()
    s["devices"]["fresh"] = {
        "label": "Fresh Device",
        "viewing_session": None,
        "view_mode": "grid",
        "last_interaction_at": now,
        "last_heartbeat_at": now,
    }
    removed = prune_devices(s, ttl_seconds=300.0)
    assert "fresh" in s["devices"]
    assert removed == []


def test_prune_devices_returns_list_of_removed_ids():
    from coordinator.state import empty_state, prune_devices

    s = empty_state()
    s["devices"]["stale1"] = {
        "label": "A", "viewing_session": None, "view_mode": "grid",
        "last_interaction_at": 0.0, "last_heartbeat_at": 0.0,
    }
    s["devices"]["stale2"] = {
        "label": "B", "viewing_session": None, "view_mode": "grid",
        "last_interaction_at": 0.0, "last_heartbeat_at": 0.0,
    }
    removed = prune_devices(s, ttl_seconds=300.0)
    assert set(removed) == {"stale1", "stale2"}
```

**Step 2: Run to verify failures**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_state.py::test_register_device_adds_new_device -v
```

Expected: `FAILED` — `ImportError: cannot import name 'register_device'`

**Step 3: Add `register_device` and `prune_devices` to `coordinator/state.py`**

Append to the bottom of `/home/bkrabach/dev/web-tmux/coordinator/state.py`:

```python
# ---------------------------------------------------------------------------
# Device management (operate on a state dict in-place)
# ---------------------------------------------------------------------------


def register_device(
    state: dict,
    device_id: str,
    label: str,
    viewing_session: str | None,
    view_mode: str,
    last_interaction_at: float,
) -> None:
    """Create or update a device entry. Always refreshes last_heartbeat_at."""
    now = time.time()
    if device_id not in state["devices"]:
        state["devices"][device_id] = empty_device(device_id, label)
    device = state["devices"][device_id]
    device["label"] = label
    device["viewing_session"] = viewing_session
    device["view_mode"] = view_mode
    device["last_interaction_at"] = last_interaction_at
    device["last_heartbeat_at"] = now


def prune_devices(state: dict, ttl_seconds: float = 300.0) -> list[str]:
    """Remove devices silent for longer than ttl_seconds. Returns removed IDs."""
    cutoff = time.time() - ttl_seconds
    stale = [
        device_id
        for device_id, device in state["devices"].items()
        if device["last_heartbeat_at"] < cutoff
    ]
    for device_id in stale:
        del state["devices"][device_id]
    return stale
```

**Step 4: Run tests to verify they pass**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_state.py -v
```

Expected: all tests pass (now ~26 tests).

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux
git add coordinator/state.py coordinator/tests/test_state.py
git commit -m "feat: state.py — device registration, heartbeat, pruning"
```

---

## Task 5: sessions.py — tmux Session Enumeration

**Files:**
- Create: `coordinator/sessions.py`
- Create: `coordinator/tests/test_sessions.py`

**Step 1: Write failing tests**

Create `/home/bkrabach/dev/web-tmux/coordinator/tests/test_sessions.py`:

```python
"""Tests for coordinator/sessions.py — tmux session enumeration."""
from unittest.mock import AsyncMock, patch

import pytest


async def test_enumerate_sessions_parses_newline_output():
    """run_tmux returns 'main\nwork\nlogs\n' → enumerate returns ['main','work','logs']."""
    with patch("coordinator.sessions.run_tmux", new_callable=AsyncMock) as mock:
        mock.return_value = "main\nwork\nlogs\n"
        from coordinator.sessions import enumerate_sessions

        result = await enumerate_sessions()
    assert result == ["main", "work", "logs"]


async def test_enumerate_sessions_returns_empty_list_when_no_sessions():
    """When tmux has no sessions, run_tmux returns empty string."""
    with patch("coordinator.sessions.run_tmux", new_callable=AsyncMock) as mock:
        mock.return_value = ""
        from coordinator.sessions import enumerate_sessions

        result = await enumerate_sessions()
    assert result == []


async def test_enumerate_sessions_strips_whitespace():
    with patch("coordinator.sessions.run_tmux", new_callable=AsyncMock) as mock:
        mock.return_value = "  main  \n  work  \n"
        from coordinator.sessions import enumerate_sessions

        result = await enumerate_sessions()
    assert result == ["main", "work"]


async def test_enumerate_sessions_handles_tmux_error():
    """If run_tmux raises RuntimeError (tmux not running), return empty list."""
    with patch("coordinator.sessions.run_tmux", new_callable=AsyncMock) as mock:
        mock.side_effect = RuntimeError("no server running")
        from coordinator.sessions import enumerate_sessions

        result = await enumerate_sessions()
    assert result == []


async def test_run_tmux_calls_correct_command():
    """run_tmux('list-sessions', '-F', '#{session_name}') calls tmux with those args."""
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"main\n", b"")
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        from coordinator.sessions import run_tmux

        await run_tmux("list-sessions", "-F", "#{session_name}")

    called_args = mock_exec.call_args[0]
    assert called_args[0] == "tmux"
    assert "list-sessions" in called_args
    assert "-F" in called_args


async def test_run_tmux_raises_on_nonzero_exit():
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"no server running on /tmp/tmux")
    mock_proc.returncode = 1

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        from coordinator.sessions import run_tmux

        with pytest.raises(RuntimeError, match="tmux.*failed"):
            await run_tmux("list-sessions")
```

**Step 2: Run tests to verify failures**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_sessions.py -v
```

Expected: `ERROR` — `ModuleNotFoundError: No module named 'coordinator.sessions'`

**Step 3: Create `coordinator/sessions.py`**

Create `/home/bkrabach/dev/web-tmux/coordinator/sessions.py`:

```python
"""
tmux session enumeration and capture-pane management.

Public API:
  run_tmux(*args)              — run any tmux command, return stdout as str
  enumerate_sessions()         — list running tmux session names
  capture_pane(name, lines)    — capture last N lines of a session's pane
  snapshot_all(names)          — capture all sessions concurrently
  update_session_cache(names)  — update in-memory session list + snapshots
  get_session_list()           — return cached session name list
  get_snapshots()              — return cached pane snapshots dict

In-memory cache (updated by poll loop, read by API endpoints):
  _session_list: list[str]           — last known session names
  _snapshots: dict[str, str]         — session_name → capture-pane text
"""

import asyncio

# ---------------------------------------------------------------------------
# In-memory cache (updated by poll loop)
# ---------------------------------------------------------------------------

_session_list: list[str] = []
_snapshots: dict[str, str] = {}


def get_session_list() -> list[str]:
    """Return the most recently cached list of session names."""
    return list(_session_list)


def get_snapshots() -> dict[str, str]:
    """Return the most recently cached capture-pane snapshots."""
    return dict(_snapshots)


async def update_session_cache(session_names: list[str]) -> None:
    """Update both _session_list and _snapshots. Called by the poll loop."""
    global _session_list, _snapshots
    _session_list = list(session_names)
    _snapshots = await snapshot_all(session_names)


# ---------------------------------------------------------------------------
# tmux subprocess helpers
# ---------------------------------------------------------------------------


async def run_tmux(*args: str) -> str:
    """Run `tmux <args>`, return stdout. Raises RuntimeError on nonzero exit."""
    proc = await asyncio.create_subprocess_exec(
        "tmux",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"tmux {list(args)} failed (exit {proc.returncode}): "
            f"{stderr.decode(errors='replace').strip()}"
        )
    return stdout.decode(errors="replace")


# ---------------------------------------------------------------------------
# Session enumeration
# ---------------------------------------------------------------------------


async def enumerate_sessions() -> list[str]:
    """Return list of running tmux session names. Returns [] if tmux not running."""
    try:
        output = await run_tmux("list-sessions", "-F", "#{session_name}")
    except RuntimeError:
        return []
    names = [line.strip() for line in output.splitlines() if line.strip()]
    return names


# ---------------------------------------------------------------------------
# Capture-pane
# ---------------------------------------------------------------------------


async def capture_pane(session_name: str, lines: int = 30) -> str:
    """
    Return the last `lines` lines of the named session's active pane.

    Uses: tmux capture-pane -p -t <name> -e 0 -l <lines>
      -p  → print to stdout
      -e 0 → no escape sequences
      -l  → limit lines

    Returns empty string if session not found or tmux not running.
    """
    try:
        output = await run_tmux(
            "capture-pane", "-p", "-t", session_name, "-e", "0", "-l", str(lines)
        )
    except RuntimeError:
        return ""
    return output


async def snapshot_all(session_names: list[str]) -> dict[str, str]:
    """Capture pane output for all sessions concurrently. Returns name→text dict."""
    if not session_names:
        return {}
    results = await asyncio.gather(
        *(capture_pane(name) for name in session_names),
        return_exceptions=True,
    )
    snapshots: dict[str, str] = {}
    for name, result in zip(session_names, results):
        if isinstance(result, Exception):
            snapshots[name] = ""
        else:
            snapshots[name] = result
    return snapshots
```

**Step 4: Run tests to verify they pass**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_sessions.py -v
```

Expected:
```
PASSED ...test_enumerate_sessions_parses_newline_output
PASSED ...test_enumerate_sessions_returns_empty_list_when_no_sessions
PASSED ...test_enumerate_sessions_strips_whitespace
PASSED ...test_enumerate_sessions_handles_tmux_error
PASSED ...test_run_tmux_calls_correct_command
PASSED ...test_run_tmux_raises_on_nonzero_exit
6 passed in 0.XXs
```

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux
git add coordinator/sessions.py coordinator/tests/test_sessions.py
git commit -m "feat: sessions.py — tmux session enumeration and run_tmux helper"
```

---

## Task 6: sessions.py — Capture-Pane Snapshots

**Files:**
- Modify: `coordinator/tests/test_sessions.py`

(The implementation was already written in Task 5. This task adds tests for `capture_pane` and `snapshot_all`.)

**Step 1: Add failing tests**

Append to `/home/bkrabach/dev/web-tmux/coordinator/tests/test_sessions.py`:

```python
# ---------------------------------------------------------------------------
# capture_pane and snapshot_all tests
# ---------------------------------------------------------------------------


async def test_capture_pane_returns_output():
    with patch("coordinator.sessions.run_tmux", new_callable=AsyncMock) as mock:
        mock.return_value = "line1\nline2\nline3\n"
        from coordinator.sessions import capture_pane

        result = await capture_pane("myapp")
    assert "line1" in result
    assert "line3" in result


async def test_capture_pane_returns_empty_string_on_error():
    with patch("coordinator.sessions.run_tmux", new_callable=AsyncMock) as mock:
        mock.side_effect = RuntimeError("session not found")
        from coordinator.sessions import capture_pane

        result = await capture_pane("ghost-session")
    assert result == ""


async def test_capture_pane_calls_correct_tmux_args():
    """Must pass -p -t <name> -e 0 -l <lines> to capture-pane."""
    captured_args: list[tuple] = []

    async def fake_run_tmux(*args: str) -> str:
        captured_args.append(args)
        return "output\n"

    with patch("coordinator.sessions.run_tmux", side_effect=fake_run_tmux):
        from coordinator.sessions import capture_pane

        await capture_pane("dev-server", lines=30)

    assert len(captured_args) == 1
    args = captured_args[0]
    assert "capture-pane" in args
    assert "-p" in args
    assert "-t" in args
    assert "dev-server" in args
    assert "-e" in args
    assert "0" in args
    assert "-l" in args
    assert "30" in args


async def test_snapshot_all_returns_dict_keyed_by_name():
    async def fake_capture(name: str, lines: int = 30) -> str:
        return f"output-for-{name}"

    with patch("coordinator.sessions.capture_pane", side_effect=fake_capture):
        from coordinator.sessions import snapshot_all

        result = await snapshot_all(["main", "work", "logs"])
    assert result["main"] == "output-for-main"
    assert result["work"] == "output-for-work"
    assert result["logs"] == "output-for-logs"


async def test_snapshot_all_returns_empty_dict_for_empty_input():
    from coordinator.sessions import snapshot_all

    result = await snapshot_all([])
    assert result == {}


async def test_snapshot_all_returns_empty_string_on_individual_failure():
    """If one session fails, its entry is '' and others succeed."""

    async def fake_capture(name: str, lines: int = 30) -> str:
        if name == "broken":
            raise RuntimeError("gone")
        return f"ok-{name}"

    with patch("coordinator.sessions.capture_pane", side_effect=fake_capture):
        from coordinator.sessions import snapshot_all

        result = await snapshot_all(["main", "broken", "work"])
    assert result["main"] == "ok-main"
    assert result["broken"] == ""
    assert result["work"] == "ok-work"
```

**Step 2: Run tests to verify they pass**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_sessions.py -v
```

Expected: all 12 tests pass.

**Step 3: Commit**

```bash
cd /home/bkrabach/dev/web-tmux
git add coordinator/tests/test_sessions.py
git commit -m "test: sessions.py — capture-pane and snapshot_all tests"
```

---

## Task 7: bells.py — Bell Flag Polling + unseen_count

**Files:**
- Create: `coordinator/bells.py`
- Create: `coordinator/tests/test_bells.py`

**Step 1: Write failing tests**

Create `/home/bkrabach/dev/web-tmux/coordinator/tests/test_bells.py`:

```python
"""Tests for coordinator/bells.py — bell detection and unseen_count."""
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def reset_bell_seen():
    """Reset in-memory _bell_seen before each test to prevent state leakage."""
    import coordinator.bells as bells_mod

    bells_mod._bell_seen.clear()
    yield
    bells_mod._bell_seen.clear()


async def test_poll_bell_flag_returns_true_when_flag_is_1():
    with patch("coordinator.bells.run_tmux", new_callable=AsyncMock) as mock:
        mock.return_value = "1\n"
        from coordinator.bells import poll_bell_flag

        result = await poll_bell_flag("myapp")
    assert result is True


async def test_poll_bell_flag_returns_false_when_flag_is_0():
    with patch("coordinator.bells.run_tmux", new_callable=AsyncMock) as mock:
        mock.return_value = "0\n"
        from coordinator.bells import poll_bell_flag

        result = await poll_bell_flag("myapp")
    assert result is False


async def test_poll_bell_flag_returns_false_on_error():
    with patch("coordinator.bells.run_tmux", new_callable=AsyncMock) as mock:
        mock.side_effect = RuntimeError("no server")
        from coordinator.bells import poll_bell_flag

        result = await poll_bell_flag("myapp")
    assert result is False


async def test_process_bell_flags_increments_unseen_count_on_new_bell():
    """0→1 transition: unseen_count goes from 0 to 1."""
    from coordinator.state import empty_bell, empty_state
    from coordinator.bells import process_bell_flags

    s = empty_state()
    s["sessions"]["myapp"] = {"bell": empty_bell()}

    async def fake_poll(name: str) -> bool:
        return True  # flag is set

    with patch("coordinator.bells.poll_bell_flag", side_effect=fake_poll):
        changed = await process_bell_flags(["myapp"], s)

    assert s["sessions"]["myapp"]["bell"]["unseen_count"] == 1
    assert s["sessions"]["myapp"]["bell"]["last_fired_at"] is not None
    assert changed is True


async def test_process_bell_flags_does_not_double_count_persistent_flag():
    """If flag stays '1' across two polls, unseen_count increments only once."""
    from coordinator.state import empty_bell, empty_state
    from coordinator.bells import process_bell_flags

    s = empty_state()
    s["sessions"]["myapp"] = {"bell": empty_bell()}

    async def fake_poll(name: str) -> bool:
        return True

    with patch("coordinator.bells.poll_bell_flag", side_effect=fake_poll):
        await process_bell_flags(["myapp"], s)
        await process_bell_flags(["myapp"], s)  # second poll — same flag

    assert s["sessions"]["myapp"]["bell"]["unseen_count"] == 1


async def test_process_bell_flags_resets_tracking_when_flag_clears():
    """After flag goes 1→0, the next 1 is a new bell and must be counted."""
    from coordinator.state import empty_bell, empty_state
    from coordinator.bells import process_bell_flags

    s = empty_state()
    s["sessions"]["myapp"] = {"bell": empty_bell()}

    # Poll 1: flag is 1 (new bell)
    with patch("coordinator.bells.poll_bell_flag", return_value=AsyncMock(return_value=True)()):
        pass  # can't easily nest like this; use the approach below

    call_count = 0

    async def fake_poll_sequence(name: str) -> bool:
        nonlocal call_count
        call_count += 1
        # First two calls: flag on; third call: flag off; fourth: flag on again
        return call_count in (1, 4)

    with patch("coordinator.bells.poll_bell_flag", side_effect=fake_poll_sequence):
        await process_bell_flags(["myapp"], s)  # count: 1 → bell fires (count=1)
        await process_bell_flags(["myapp"], s)  # count: 2 → flag off (count resets)
        await process_bell_flags(["myapp"], s)  # count: 3 — wait, this is wrong

    # Simpler approach: call the function directly, manipulating _bell_seen
    import coordinator.bells as bells_mod

    s2 = empty_state()
    s2["sessions"]["myapp"] = {"bell": empty_bell()}
    bells_mod._bell_seen.clear()

    # Simulate: first bell fires
    bells_mod._bell_seen["myapp"] = False
    with patch("coordinator.bells.poll_bell_flag", new_callable=AsyncMock) as mock:
        mock.return_value = True
        await process_bell_flags(["myapp"], s2)
    assert s2["sessions"]["myapp"]["bell"]["unseen_count"] == 1

    # Simulate: flag clears
    with patch("coordinator.bells.poll_bell_flag", new_callable=AsyncMock) as mock:
        mock.return_value = False
        await process_bell_flags(["myapp"], s2)

    # Simulate: new bell fires
    with patch("coordinator.bells.poll_bell_flag", new_callable=AsyncMock) as mock:
        mock.return_value = True
        await process_bell_flags(["myapp"], s2)
    assert s2["sessions"]["myapp"]["bell"]["unseen_count"] == 2


async def test_process_bell_flags_no_change_returns_false():
    """If nothing changed, return False."""
    from coordinator.state import empty_bell, empty_state
    from coordinator.bells import process_bell_flags

    s = empty_state()
    s["sessions"]["quiet"] = {"bell": empty_bell()}

    with patch("coordinator.bells.poll_bell_flag", new_callable=AsyncMock) as mock:
        mock.return_value = False
        changed = await process_bell_flags(["quiet"], s)

    assert changed is False


async def test_process_bell_flags_creates_bell_entry_if_missing():
    """If sessions[name] exists but has no 'bell' key, it should be created."""
    from coordinator.state import empty_state
    from coordinator.bells import process_bell_flags

    s = empty_state()
    s["sessions"]["myapp"] = {}  # no 'bell' key

    with patch("coordinator.bells.poll_bell_flag", new_callable=AsyncMock) as mock:
        mock.return_value = False
        await process_bell_flags(["myapp"], s)

    assert "bell" in s["sessions"]["myapp"]
```

**Step 2: Run to verify failures**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_bells.py -v
```

Expected: `ERROR` — `ModuleNotFoundError: No module named 'coordinator.bells'`

**Step 3: Create `coordinator/bells.py`**

Create `/home/bkrabach/dev/web-tmux/coordinator/bells.py`:

```python
"""
Bell detection and acknowledgement for the tmux-web coordinator.

SPIKE FINDING (run coordinator/spike_bell_flag.py to confirm):
  `tmux display-message -t {session} -p "#{window_bell_flag}"` does NOT clear
  the bell flag. The flag persists until the window is visited inside tmux.

  Consequence: we must track the PREVIOUS state of each session's bell flag
  in-memory (_bell_seen dict) to detect 0→1 transitions and avoid double-counting.

Bell clear rule:
  A session's bells are globally acknowledged (unseen_count reset) when:
    1. The session is open in FULLSCREEN on any connected device, AND
    2. That device had a user interaction within the last 60 seconds.

  See: should_clear_bell(), apply_bell_clear_rule()

# NOTE FOR PHASE 2 (frontend):
  The backtick (`) key returns from fullscreen to grid view.
  Fullscreen ↔ grid transitions update view_mode in heartbeat payloads.
"""

import time

from coordinator.sessions import run_tmux
from coordinator.state import empty_bell

# ---------------------------------------------------------------------------
# In-memory state — tracks previous bell flag per session to detect transitions
# ---------------------------------------------------------------------------

_bell_seen: dict[str, bool] = {}

# ---------------------------------------------------------------------------
# Bell flag polling
# ---------------------------------------------------------------------------


async def poll_bell_flag(session_name: str) -> bool:
    """
    Return True if the tmux window_bell_flag is set for this session.

    Command: tmux display-message -t <name> -p "#{window_bell_flag}"
    Returns: "1\n" or "0\n"

    Returns False on any error (tmux not running, session gone, etc.).
    """
    try:
        output = await run_tmux(
            "display-message", "-t", session_name, "-p", "#{window_bell_flag}"
        )
        return output.strip() == "1"
    except RuntimeError:
        return False


# ---------------------------------------------------------------------------
# Bell processing
# ---------------------------------------------------------------------------


async def process_bell_flags(session_names: list[str], state: dict) -> bool:
    """
    Poll bell flags for all sessions, update state in-place.

    Detects 0→1 transitions using _bell_seen. Increments unseen_count and
    sets last_fired_at on new bells. Resets tracking when flag goes to 0.

    Returns True if any bell state changed.
    """
    changed = False
    now = time.time()

    for name in session_names:
        # Ensure bell sub-dict exists
        if "bell" not in state["sessions"].get(name, {}):
            if name not in state["sessions"]:
                state["sessions"][name] = {}
            state["sessions"][name]["bell"] = empty_bell()

        bell = state["sessions"][name]["bell"]
        flag_set = await poll_bell_flag(name)
        previously_seen = _bell_seen.get(name, False)

        if flag_set and not previously_seen:
            # 0 → 1 transition: new bell
            bell["unseen_count"] += 1
            bell["last_fired_at"] = now
            _bell_seen[name] = True
            changed = True
        elif not flag_set:
            # Flag is off — reset tracking so next 1 counts as new
            if previously_seen:
                _bell_seen[name] = False
                # Do NOT decrement unseen_count — it's a count of unacknowledged bells

    return changed
```

**Step 4: Run tests to verify they pass**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_bells.py -v
```

Expected: all 8 tests pass.

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux
git add coordinator/bells.py coordinator/tests/test_bells.py
git commit -m "feat: bells.py — bell flag polling and unseen_count tracking"
```

---

## Task 8: bells.py — Active-Device Gate Rule (Bell Clear)

**Files:**
- Modify: `coordinator/bells.py`
- Modify: `coordinator/tests/test_bells.py`

**Step 1: Add failing tests**

Append to `/home/bkrabach/dev/web-tmux/coordinator/tests/test_bells.py`:

```python
# ---------------------------------------------------------------------------
# Active-device gate rule tests
# ---------------------------------------------------------------------------

import time as time_module


def _make_state_with_bell(session: str, unseen: int, device_id: str = "d-abc",
                           view_mode: str = "fullscreen",
                           seconds_since_interaction: float = 10.0) -> dict:
    """Helper: build a minimal state dict for bell-clear tests."""
    from coordinator.state import empty_state, empty_bell

    s = empty_state()
    s["sessions"][session] = {"bell": empty_bell()}
    s["sessions"][session]["bell"]["unseen_count"] = unseen
    s["sessions"][session]["bell"]["last_fired_at"] = time_module.time() - 5

    now = time_module.time()
    s["devices"][device_id] = {
        "label": "Test Device",
        "viewing_session": session,
        "view_mode": view_mode,
        "last_interaction_at": now - seconds_since_interaction,
        "last_heartbeat_at": now,
    }
    return s


def test_should_clear_bell_returns_true_for_fullscreen_recent_interaction():
    from coordinator.bells import should_clear_bell

    s = _make_state_with_bell("myapp", unseen=1, view_mode="fullscreen",
                               seconds_since_interaction=10.0)
    assert should_clear_bell("myapp", s) is True


def test_should_clear_bell_returns_false_for_grid_mode():
    from coordinator.bells import should_clear_bell

    s = _make_state_with_bell("myapp", unseen=1, view_mode="grid",
                               seconds_since_interaction=10.0)
    assert should_clear_bell("myapp", s) is False


def test_should_clear_bell_returns_false_when_interaction_too_old():
    from coordinator.bells import should_clear_bell

    s = _make_state_with_bell("myapp", unseen=1, view_mode="fullscreen",
                               seconds_since_interaction=90.0)  # > 60s threshold
    assert should_clear_bell("myapp", s) is False


def test_should_clear_bell_returns_false_when_device_viewing_different_session():
    from coordinator.bells import should_clear_bell
    from coordinator.state import empty_state, empty_bell

    s = empty_state()
    s["sessions"]["myapp"] = {"bell": empty_bell()}
    s["sessions"]["myapp"]["bell"]["unseen_count"] = 1
    now = time_module.time()
    s["devices"]["d-abc"] = {
        "label": "Test",
        "viewing_session": "other-session",  # different session!
        "view_mode": "fullscreen",
        "last_interaction_at": now - 5,
        "last_heartbeat_at": now,
    }
    assert should_clear_bell("myapp", s) is False


def test_should_clear_bell_returns_false_when_no_devices():
    from coordinator.bells import should_clear_bell
    from coordinator.state import empty_state, empty_bell

    s = empty_state()
    s["sessions"]["myapp"] = {"bell": empty_bell()}
    s["sessions"]["myapp"]["bell"]["unseen_count"] = 1
    assert should_clear_bell("myapp", s) is False


def test_apply_bell_clear_rule_clears_matching_sessions():
    from coordinator.bells import apply_bell_clear_rule

    s = _make_state_with_bell("myapp", unseen=3, view_mode="fullscreen",
                               seconds_since_interaction=5.0)
    cleared = apply_bell_clear_rule(s)
    assert "myapp" in cleared
    assert s["sessions"]["myapp"]["bell"]["unseen_count"] == 0
    assert s["sessions"]["myapp"]["bell"]["seen_at"] is not None


def test_apply_bell_clear_rule_skips_sessions_with_zero_unseen():
    from coordinator.bells import apply_bell_clear_rule
    from coordinator.state import empty_state, empty_bell

    s = empty_state()
    s["sessions"]["quiet"] = {"bell": empty_bell()}
    # unseen_count is already 0 — nothing to clear
    cleared = apply_bell_clear_rule(s)
    assert "quiet" not in cleared


def test_apply_bell_clear_rule_returns_list_of_cleared_session_names():
    from coordinator.bells import apply_bell_clear_rule

    s = _make_state_with_bell("myapp", unseen=1, view_mode="fullscreen",
                               seconds_since_interaction=5.0)
    cleared = apply_bell_clear_rule(s)
    assert isinstance(cleared, list)
    assert "myapp" in cleared


def test_apply_bell_clear_rule_resets_bell_seen_tracking():
    """Clearing a bell must also reset _bell_seen so next bell is counted fresh."""
    import coordinator.bells as bells_mod
    from coordinator.bells import apply_bell_clear_rule

    bells_mod._bell_seen["myapp"] = True
    s = _make_state_with_bell("myapp", unseen=1, view_mode="fullscreen",
                               seconds_since_interaction=5.0)
    apply_bell_clear_rule(s)
    assert bells_mod._bell_seen.get("myapp") is False
```

**Step 2: Run to verify failures**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_bells.py::test_should_clear_bell_returns_true_for_fullscreen_recent_interaction -v
```

Expected: `FAILED` — `ImportError: cannot import name 'should_clear_bell'`

**Step 3: Add `should_clear_bell` and `apply_bell_clear_rule` to `coordinator/bells.py`**

Append to the bottom of `/home/bkrabach/dev/web-tmux/coordinator/bells.py`:

```python
# ---------------------------------------------------------------------------
# Bell clear rule — active-device gate
# ---------------------------------------------------------------------------

_INTERACTION_WINDOW_SECONDS: float = 60.0


def should_clear_bell(session_name: str, state: dict) -> bool:
    """
    Return True if this session's bells should be globally cleared.

    Rule: any connected device must be viewing this session in FULLSCREEN
    AND have had a user interaction within the last 60 seconds.
    """
    now = time.time()
    for device in state["devices"].values():
        if (
            device["viewing_session"] == session_name
            and device["view_mode"] == "fullscreen"
            and device["last_interaction_at"] > now - _INTERACTION_WINDOW_SECONDS
        ):
            return True
    return False


def apply_bell_clear_rule(state: dict) -> list[str]:
    """
    Check every session with unseen bells against the active-device gate rule.

    For sessions that qualify, reset unseen_count to 0, set seen_at to now,
    and reset _bell_seen so the next actual bell is counted fresh.

    Returns the list of session names that were cleared.
    """
    now = time.time()
    cleared: list[str] = []

    for name, session_data in state["sessions"].items():
        bell = session_data.get("bell", {})
        if bell.get("unseen_count", 0) == 0:
            continue  # nothing to clear
        if should_clear_bell(name, state):
            bell["unseen_count"] = 0
            bell["seen_at"] = now
            _bell_seen[name] = False  # reset so next bell counts fresh
            cleared.append(name)

    return cleared
```

**Step 4: Run all bell tests**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_bells.py -v
```

Expected: all 17 tests pass.

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux
git add coordinator/bells.py coordinator/tests/test_bells.py
git commit -m "feat: bells.py — active-device gate rule and global bell clear"
```

---

## Task 9: ttyd.py — Spawn ttyd Process

**Files:**
- Create: `coordinator/ttyd.py`
- Create: `coordinator/tests/test_ttyd.py`

**Step 1: Write failing tests**

Create `/home/bkrabach/dev/web-tmux/coordinator/tests/test_ttyd.py`:

```python
"""Tests for coordinator/ttyd.py — ttyd process lifecycle."""
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def use_tmp_pid_dir(tmp_path, monkeypatch):
    """Redirect PID file operations to a temp directory."""
    import coordinator.ttyd as ttyd_mod

    monkeypatch.setattr(ttyd_mod, "TTYD_PID_DIR", tmp_path)
    monkeypatch.setattr(ttyd_mod, "TTYD_PID_PATH", tmp_path / "ttyd.pid")


async def test_spawn_ttyd_writes_pid_file(tmp_path):
    import coordinator.ttyd as ttyd_mod

    mock_proc = MagicMock()
    mock_proc.pid = 12345

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        await ttyd_mod.spawn_ttyd("myapp")

    pid_file = tmp_path / "ttyd.pid"
    assert pid_file.exists()
    assert pid_file.read_text().strip() == "12345"


async def test_spawn_ttyd_uses_correct_command():
    import coordinator.ttyd as ttyd_mod

    mock_proc = MagicMock()
    mock_proc.pid = 99

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        await ttyd_mod.spawn_ttyd("work")

    args = mock_exec.call_args[0]
    assert "ttyd" in args
    assert "-W" in args
    assert "-m" in args
    assert "3" in args
    assert "-p" in args
    assert "7682" in args
    assert "tmux" in args
    assert "attach" in args
    assert "-t" in args
    assert "work" in args


async def test_spawn_ttyd_returns_process_object():
    import coordinator.ttyd as ttyd_mod

    mock_proc = MagicMock()
    mock_proc.pid = 42

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await ttyd_mod.spawn_ttyd("logs")

    assert result is mock_proc
```

**Step 2: Run to verify failures**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_ttyd.py -v
```

Expected: `ERROR` — `ModuleNotFoundError: No module named 'coordinator.ttyd'`

**Step 3: Create `coordinator/ttyd.py` with spawn**

Create `/home/bkrabach/dev/web-tmux/coordinator/ttyd.py`:

```python
"""
ttyd process lifecycle management.

One ttyd instance runs at a time on TTYD_PORT (7682). It is spawned when a
user connects to a session and killed when they disconnect or switch sessions.

PID file: ~/.local/share/tmux-web/ttyd.pid
  (Override TTYD_PID_DIR with env var TMUX_WEB_STATE_DIR for testing.)

ttyd command:
  ttyd -W -m 3 -p 7682 tmux attach -t <session_name>
    -W      writable mode
    -m 3    allow up to 3 simultaneous connections
    -p 7682 listen on port 7682

Orphan detection (on coordinator startup):
  If a PID file exists from a previous run, the old ttyd process is still
  running (ttyd is persistent, not tied to browser sessions). On startup,
  coordinator reads the PID file and kills the orphaned process before
  registering fresh state.
"""

import asyncio
import os
import pathlib
import signal

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_pid_dir_override = os.environ.get("TMUX_WEB_STATE_DIR")
TTYD_PID_DIR: pathlib.Path = (
    pathlib.Path(_pid_dir_override)
    if _pid_dir_override
    else pathlib.Path.home() / ".local" / "share" / "tmux-web"
)
TTYD_PID_PATH: pathlib.Path = TTYD_PID_DIR / "ttyd.pid"
TTYD_PORT: int = 7682

# ---------------------------------------------------------------------------
# Active process handle (in-memory, cleared when killed)
# ---------------------------------------------------------------------------

_active_process: asyncio.subprocess.Process | None = None

# ---------------------------------------------------------------------------
# Spawn
# ---------------------------------------------------------------------------


async def spawn_ttyd(session_name: str) -> asyncio.subprocess.Process:
    """
    Spawn `ttyd -W -m 3 -p 7682 tmux attach -t <session_name>`.

    Writes the PID to TTYD_PID_PATH. Stores the process handle in
    _active_process. Caller is responsible for killing any existing
    ttyd process before calling spawn (see kill_ttyd).
    """
    global _active_process

    TTYD_PID_DIR.mkdir(parents=True, exist_ok=True)

    proc = await asyncio.create_subprocess_exec(
        "ttyd",
        "-W",
        "-m", "3",
        "-p", str(TTYD_PORT),
        "tmux", "attach", "-t", session_name,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    TTYD_PID_PATH.write_text(str(proc.pid))
    _active_process = proc
    return proc
```

**Step 4: Run tests to verify they pass**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_ttyd.py::test_spawn_ttyd_writes_pid_file -v
pytest coordinator/tests/test_ttyd.py::test_spawn_ttyd_uses_correct_command -v
pytest coordinator/tests/test_ttyd.py::test_spawn_ttyd_returns_process_object -v
```

Expected: all 3 tests pass.

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux
git add coordinator/ttyd.py coordinator/tests/test_ttyd.py
git commit -m "feat: ttyd.py — spawn ttyd process and write PID file"
```

---

## Task 10: ttyd.py — Kill and PID File Cleanup

**Files:**
- Modify: `coordinator/ttyd.py`
- Modify: `coordinator/tests/test_ttyd.py`

**Step 1: Add failing tests**

Append to `/home/bkrabach/dev/web-tmux/coordinator/tests/test_ttyd.py`:

```python
# ---------------------------------------------------------------------------
# kill_ttyd tests
# ---------------------------------------------------------------------------

import signal
import asyncio


async def test_kill_ttyd_returns_false_when_no_pid_file(tmp_path):
    import coordinator.ttyd as ttyd_mod

    # No PID file exists — nothing to kill
    result = await ttyd_mod.kill_ttyd()
    assert result is False


async def test_kill_ttyd_reads_pid_file_and_sends_sigterm(tmp_path):
    import coordinator.ttyd as ttyd_mod

    pid_file = tmp_path / "ttyd.pid"
    pid_file.write_text("99999")  # fake PID

    with patch("os.kill") as mock_kill, \
         patch("asyncio.sleep", new_callable=AsyncMock):
        # os.kill with signal 0 checks process existence — make it pass
        # os.kill with SIGTERM kills it
        def side_effect(pid: int, sig: int) -> None:
            if sig == 0:
                return  # process exists
            # SIGTERM send — do nothing (fake)

        mock_kill.side_effect = side_effect
        result = await ttyd_mod.kill_ttyd()

    assert result is True
    assert mock_kill.called


async def test_kill_ttyd_removes_pid_file(tmp_path):
    import coordinator.ttyd as ttyd_mod

    pid_file = tmp_path / "ttyd.pid"
    pid_file.write_text("99999")

    with patch("os.kill", side_effect=lambda pid, sig: None), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        await ttyd_mod.kill_ttyd()

    assert not pid_file.exists()


async def test_kill_ttyd_handles_process_already_dead(tmp_path):
    """If process is gone (ProcessLookupError), remove PID file and return True."""
    import coordinator.ttyd as ttyd_mod

    pid_file = tmp_path / "ttyd.pid"
    pid_file.write_text("99999")

    def fake_kill(pid: int, sig: int) -> None:
        raise ProcessLookupError("no such process")

    with patch("os.kill", side_effect=fake_kill):
        result = await ttyd_mod.kill_ttyd()

    assert result is True
    assert not pid_file.exists()
```

**Step 2: Run to verify failures**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_ttyd.py::test_kill_ttyd_returns_false_when_no_pid_file -v
```

Expected: `FAILED` — `ImportError: cannot import name... kill_ttyd`

**Step 3: Add `kill_ttyd` to `coordinator/ttyd.py`**

Append to the bottom of `/home/bkrabach/dev/web-tmux/coordinator/ttyd.py`:

```python
# ---------------------------------------------------------------------------
# Kill
# ---------------------------------------------------------------------------


async def kill_ttyd() -> bool:
    """
    Kill the currently running ttyd process.

    Reads PID from TTYD_PID_PATH, sends SIGTERM, waits up to 2 seconds for
    the process to exit, then removes the PID file.

    Returns True if a process was killed (or was already dead), False if
    no PID file existed.
    """
    global _active_process

    if not TTYD_PID_PATH.exists():
        return False

    pid_text = TTYD_PID_PATH.read_text().strip()
    try:
        pid = int(pid_text)
    except ValueError:
        TTYD_PID_PATH.unlink(missing_ok=True)
        _active_process = None
        return False

    try:
        os.kill(pid, 0)  # Check if process exists (raises if not)
    except ProcessLookupError:
        # Already dead — clean up the PID file
        TTYD_PID_PATH.unlink(missing_ok=True)
        _active_process = None
        return True

    try:
        os.kill(pid, signal.SIGTERM)
        # Give the process up to 2 seconds to exit
        for _ in range(20):
            await asyncio.sleep(0.1)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break  # Process exited
    except ProcessLookupError:
        pass  # Process already gone

    TTYD_PID_PATH.unlink(missing_ok=True)
    _active_process = None
    return True
```

**Step 4: Run all ttyd tests**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_ttyd.py -v
```

Expected: all 7 tests pass.

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux
git add coordinator/ttyd.py coordinator/tests/test_ttyd.py
git commit -m "feat: ttyd.py — kill_ttyd with SIGTERM and PID file cleanup"
```

---

## Task 11: ttyd.py — Orphan Detection on Startup

**Files:**
- Modify: `coordinator/ttyd.py`
- Modify: `coordinator/tests/test_ttyd.py`

**Step 1: Add failing tests**

Append to `/home/bkrabach/dev/web-tmux/coordinator/tests/test_ttyd.py`:

```python
# ---------------------------------------------------------------------------
# kill_orphan_ttyd tests
# ---------------------------------------------------------------------------


async def test_kill_orphan_ttyd_returns_false_when_no_pid_file(tmp_path):
    import coordinator.ttyd as ttyd_mod

    result = await ttyd_mod.kill_orphan_ttyd()
    assert result is False


async def test_kill_orphan_ttyd_kills_running_process(tmp_path):
    import coordinator.ttyd as ttyd_mod

    pid_file = tmp_path / "ttyd.pid"
    pid_file.write_text("88888")

    with patch("os.kill", side_effect=lambda pid, sig: None), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        result = await ttyd_mod.kill_orphan_ttyd()

    assert result is True
    assert not pid_file.exists()


async def test_kill_orphan_ttyd_handles_pid_file_with_dead_process(tmp_path):
    """PID file exists but process is already dead — still return True, remove file."""
    import coordinator.ttyd as ttyd_mod

    pid_file = tmp_path / "ttyd.pid"
    pid_file.write_text("77777")

    def fake_kill(pid: int, sig: int) -> None:
        raise ProcessLookupError("no such process")

    with patch("os.kill", side_effect=fake_kill):
        result = await ttyd_mod.kill_orphan_ttyd()

    assert result is True
    assert not pid_file.exists()


async def test_kill_orphan_ttyd_handles_invalid_pid_file_content(tmp_path):
    """PID file contains garbage — remove it, return False."""
    import coordinator.ttyd as ttyd_mod

    pid_file = tmp_path / "ttyd.pid"
    pid_file.write_text("not-a-pid")

    result = await ttyd_mod.kill_orphan_ttyd()
    assert result is False
    assert not pid_file.exists()
```

**Step 2: Run to verify failures**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_ttyd.py::test_kill_orphan_ttyd_returns_false_when_no_pid_file -v
```

Expected: `FAILED` — `ImportError: cannot import name... kill_orphan_ttyd`

**Step 3: Add `kill_orphan_ttyd` to `coordinator/ttyd.py`**

Append to the bottom of `/home/bkrabach/dev/web-tmux/coordinator/ttyd.py`:

```python
# ---------------------------------------------------------------------------
# Orphan detection (run on startup)
# ---------------------------------------------------------------------------


async def kill_orphan_ttyd() -> bool:
    """
    On coordinator startup: check for a stale PID file from a previous run.

    If found, kill the process (if still running) and remove the PID file.
    This prevents two ttyd instances running simultaneously after a coordinator
    restart or crash.

    Returns True if an orphan was found (dead or alive), False if no PID file.
    """
    return await kill_ttyd()
```

**Step 4: Run all ttyd tests**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_ttyd.py -v
```

Expected: all 11 tests pass.

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux
git add coordinator/ttyd.py coordinator/tests/test_ttyd.py
git commit -m "feat: ttyd.py — orphan detection on coordinator startup"
```

---

## Task 12: main.py — FastAPI Skeleton, Lifespan, Health Endpoint

**Files:**
- Create: `coordinator/main.py`
- Create: `coordinator/tests/test_api.py`

**Step 1: Write failing tests**

Create `/home/bkrabach/dev/web-tmux/coordinator/tests/test_api.py`:

```python
"""Tests for coordinator/main.py — FastAPI endpoints."""
import json
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def patch_startup_and_state(tmp_path, monkeypatch):
    """
    Prevent lifespan from touching tmux, ttyd, or production state files.

    - Redirects state files to tmp_path
    - Mocks kill_orphan_ttyd (no real ttyd)
    - Replaces poll loop with a no-op (no real tmux)
    """
    import coordinator.state as state_mod
    import coordinator.ttyd as ttyd_mod

    monkeypatch.setattr(state_mod, "STATE_DIR", tmp_path)
    monkeypatch.setattr(state_mod, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(ttyd_mod, "TTYD_PID_DIR", tmp_path)
    monkeypatch.setattr(ttyd_mod, "TTYD_PID_PATH", tmp_path / "ttyd.pid")

    with patch("coordinator.ttyd.kill_orphan_ttyd", new_callable=AsyncMock), \
         patch("coordinator.main._poll_loop", new_callable=AsyncMock):
        yield


@pytest.fixture
def client():
    """Return a TestClient with lifespan running."""
    from coordinator.main import app

    with TestClient(app) as c:
        yield c


def test_health_returns_200(client):
    response = client.get("/health")
    assert response.status_code == 200


def test_health_returns_ok_status(client):
    response = client.get("/health")
    data = response.json()
    assert data["status"] == "ok"
```

**Step 2: Run to verify failures**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_api.py::test_health_returns_200 -v
```

Expected: `ERROR` — `ModuleNotFoundError: No module named 'coordinator.main'`

**Step 3: Create `coordinator/main.py`**

Create `/home/bkrabach/dev/web-tmux/coordinator/main.py`:

```python
"""
Web-Tmux Dashboard — Session Coordinator

FastAPI server on port 8080. Serves JSON API for the dashboard frontend.

Startup (lifespan):
  1. Kill any orphaned ttyd process from previous run
  2. Start background poll loop (runs every POLL_INTERVAL seconds)

Poll loop (every 2s by default, configurable via POLL_INTERVAL env var):
  - Enumerate tmux sessions
  - Update capture-pane snapshots (in-memory cache)
  - Process bell flags (detect 0→1 transitions, update unseen_count)
  - Apply bell clear rule (active-device gate)
  - Prune stale devices (> 5 min without heartbeat)
  - Atomically write updated state.json

Endpoints:
  GET  /health                          — liveness check
  GET  /api/state                       — full persistent state
  PATCH /api/state                      — update session_order
  GET  /api/sessions                    — sessions + snapshots + bell flags
  POST /api/sessions/{name}/connect     — spawn ttyd for named session
  DELETE /api/sessions/current          — kill ttyd, clear active session
  POST /api/heartbeat                   — device heartbeat + view state

# NOTE (Phase 2):
  The backtick (`) key toggles fullscreen → grid. When frontend implements
  this, it must send a heartbeat with view_mode="grid" immediately on toggle.
"""

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from coordinator import bells, sessions, state, ttyd

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

POLL_INTERVAL: float = float(os.environ.get("POLL_INTERVAL", "2.0"))

# ---------------------------------------------------------------------------
# Background poll loop
# ---------------------------------------------------------------------------

_poll_task: asyncio.Task | None = None


async def _run_poll_cycle() -> None:
    """
    Single poll cycle: enumerate sessions, update snapshots, process bells,
    apply clear rule, prune devices, write state atomically.
    """
    session_names = await sessions.enumerate_sessions()
    await sessions.update_session_cache(session_names)

    existing = set(session_names)

    async with state.state_lock:
        current = state.load_state()

        # Reconcile session_order: preserve user order, add new, remove deleted
        known_order = current["session_order"]
        new_order = [s for s in known_order if s in existing]
        new_sessions_to_add = [s for s in session_names if s not in set(known_order)]
        new_order.extend(new_sessions_to_add)
        current["session_order"] = new_order

        # Ensure every session has a bell entry
        for name in session_names:
            if name not in current["sessions"]:
                current["sessions"][name] = {"bell": state.empty_bell()}

        # Remove sessions that no longer exist in tmux
        current["sessions"] = {
            k: v for k, v in current["sessions"].items() if k in existing
        }

        # Clear active_session if that session is gone
        if current["active_session"] not in existing:
            current["active_session"] = None

        # Process bell flags and apply clear rule
        await bells.process_bell_flags(session_names, current)
        bells.apply_bell_clear_rule(current)

        # Prune stale devices
        state.prune_devices(current)

        # Write back
        state.save_state(current)


async def _poll_loop() -> None:
    """Runs _run_poll_cycle() every POLL_INTERVAL seconds indefinitely."""
    while True:
        try:
            await _run_poll_cycle()
        except Exception:
            pass  # Log but don't crash the loop
        await asyncio.sleep(POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _poll_task
    await ttyd.kill_orphan_ttyd()
    _poll_task = asyncio.create_task(_poll_loop())
    yield
    if _poll_task:
        _poll_task.cancel()
        try:
            await _poll_task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="tmux-web coordinator",
    version="0.1.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict:
    """Liveness check."""
    return {"status": "ok"}
```

**Step 4: Run tests to verify they pass**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_api.py::test_health_returns_200 coordinator/tests/test_api.py::test_health_returns_ok_status -v
```

Expected:
```
PASSED coordinator/tests/test_api.py::test_health_returns_200
PASSED coordinator/tests/test_api.py::test_health_returns_ok_status
2 passed in 0.XXs
```

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux
git add coordinator/main.py coordinator/tests/test_api.py
git commit -m "feat: main.py — FastAPI skeleton, lifespan, poll loop, /health"
```

---

## Task 13: main.py — GET /api/state and PATCH /api/state

**Files:**
- Modify: `coordinator/main.py`
- Modify: `coordinator/tests/test_api.py`

**Step 1: Add failing tests**

Append to `/home/bkrabach/dev/web-tmux/coordinator/tests/test_api.py`:

```python
# ---------------------------------------------------------------------------
# GET /api/state and PATCH /api/state
# ---------------------------------------------------------------------------


def test_get_state_returns_full_state(client):
    response = client.get("/api/state")
    assert response.status_code == 200
    data = response.json()
    assert "active_session" in data
    assert "session_order" in data
    assert "sessions" in data
    assert "devices" in data


def test_get_state_active_session_is_none_initially(client):
    response = client.get("/api/state")
    data = response.json()
    assert data["active_session"] is None


def test_patch_state_updates_session_order(client, tmp_path):
    # Write initial state with some sessions
    import coordinator.state as state_mod

    initial = state_mod.empty_state()
    initial["session_order"] = ["main", "work", "logs"]
    state_mod.save_state(initial)

    response = client.patch(
        "/api/state",
        json={"session_order": ["logs", "work", "main"]},
    )
    assert response.status_code == 200

    # Verify persisted
    check = client.get("/api/state")
    assert check.json()["session_order"] == ["logs", "work", "main"]


def test_patch_state_rejects_non_list_session_order(client):
    response = client.patch(
        "/api/state",
        json={"session_order": "not-a-list"},
    )
    assert response.status_code == 422


def test_patch_state_ignores_unknown_fields(client):
    """PATCH only updates known mutable fields; unknown fields are silently ignored."""
    response = client.patch(
        "/api/state",
        json={"session_order": [], "unknown_field": "value"},
    )
    assert response.status_code == 200
```

**Step 2: Run to verify failures**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_api.py::test_get_state_returns_full_state -v
```

Expected: `FAILED` — 404 Not Found (endpoint doesn't exist yet)

**Step 3: Add endpoints to `coordinator/main.py`**

Add these imports and endpoint definitions after the `/health` endpoint in `/home/bkrabach/dev/web-tmux/coordinator/main.py`:

```python
# Add to imports at the top of main.py:
from pydantic import BaseModel


# Add after the /health endpoint:

class StatePatch(BaseModel):
    session_order: list[str]


@app.get("/api/state")
async def get_state() -> dict:
    """Return the full persistent state."""
    return await state.read_state()


@app.patch("/api/state")
async def patch_state(patch: StatePatch) -> dict:
    """Update mutable state fields. Currently supports session_order only."""
    async with state.state_lock:
        current = state.load_state()
        current["session_order"] = patch.session_order
        state.save_state(current)
    return current
```

**Step 4: Run tests**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_api.py -v -k "state"
```

Expected: all 5 state tests pass.

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux
git add coordinator/main.py coordinator/tests/test_api.py
git commit -m "feat: main.py — GET /api/state and PATCH /api/state endpoints"
```

---

## Task 14: main.py — GET /api/sessions

**Files:**
- Modify: `coordinator/main.py`
- Modify: `coordinator/tests/test_api.py`

**Step 1: Add failing tests**

Append to `/home/bkrabach/dev/web-tmux/coordinator/tests/test_api.py`:

```python
# ---------------------------------------------------------------------------
# GET /api/sessions
# ---------------------------------------------------------------------------


def test_get_sessions_returns_list(client):
    with patch("coordinator.sessions.get_session_list", return_value=["main", "work"]), \
         patch("coordinator.sessions.get_snapshots",
               return_value={"main": "output\n", "work": "other\n"}):
        response = client.get("/api/sessions")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_get_sessions_each_item_has_required_fields(client):
    with patch("coordinator.sessions.get_session_list", return_value=["main"]), \
         patch("coordinator.sessions.get_snapshots", return_value={"main": "output\n"}):
        response = client.get("/api/sessions")
    data = response.json()
    assert len(data) == 1
    session = data[0]
    assert "name" in session
    assert "snapshot" in session
    assert "bell" in session


def test_get_sessions_includes_snapshot_text(client):
    with patch("coordinator.sessions.get_session_list", return_value=["myapp"]), \
         patch("coordinator.sessions.get_snapshots",
               return_value={"myapp": "hello world\n"}):
        response = client.get("/api/sessions")
    data = response.json()
    assert data[0]["snapshot"] == "hello world\n"


def test_get_sessions_includes_bell_state(client, tmp_path):
    import coordinator.state as state_mod

    s = state_mod.empty_state()
    s["sessions"]["myapp"] = {"bell": state_mod.empty_bell()}
    s["sessions"]["myapp"]["bell"]["unseen_count"] = 2
    state_mod.save_state(s)

    with patch("coordinator.sessions.get_session_list", return_value=["myapp"]), \
         patch("coordinator.sessions.get_snapshots", return_value={"myapp": ""}):
        response = client.get("/api/sessions")
    data = response.json()
    assert data[0]["bell"]["unseen_count"] == 2


def test_get_sessions_returns_empty_list_when_no_sessions(client):
    with patch("coordinator.sessions.get_session_list", return_value=[]), \
         patch("coordinator.sessions.get_snapshots", return_value={}):
        response = client.get("/api/sessions")
    assert response.json() == []
```

**Step 2: Run to verify failures**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_api.py::test_get_sessions_returns_list -v
```

Expected: `FAILED` — 404 Not Found

**Step 3: Add `GET /api/sessions` to `coordinator/main.py`**

Append after the `patch_state` endpoint in `/home/bkrabach/dev/web-tmux/coordinator/main.py`:

```python
@app.get("/api/sessions")
async def get_sessions() -> list[dict]:
    """
    Return all known tmux sessions with their capture-pane snapshots and bell state.

    Data comes from:
      - sessions.get_session_list() — cached session names (updated by poll loop)
      - sessions.get_snapshots()    — cached capture-pane output (updated by poll loop)
      - state.read_state()          — persistent state (bell counts, etc.)
    """
    session_names = sessions.get_session_list()
    snapshots = sessions.get_snapshots()
    current_state = await state.read_state()

    result: list[dict] = []
    for name in session_names:
        bell_data = (
            current_state.get("sessions", {})
            .get(name, {})
            .get("bell", state.empty_bell())
        )
        result.append({
            "name": name,
            "snapshot": snapshots.get(name, ""),
            "bell": bell_data,
        })
    return result
```

**Step 4: Run tests**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_api.py -v -k "sessions"
```

Expected: all 5 sessions tests pass.

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux
git add coordinator/main.py coordinator/tests/test_api.py
git commit -m "feat: main.py — GET /api/sessions endpoint"
```

---

## Task 15: main.py — POST /api/sessions/{name}/connect and DELETE /api/sessions/current

**Files:**
- Modify: `coordinator/main.py`
- Modify: `coordinator/tests/test_api.py`

**Step 1: Add failing tests**

Append to `/home/bkrabach/dev/web-tmux/coordinator/tests/test_api.py`:

```python
# ---------------------------------------------------------------------------
# POST /api/sessions/{name}/connect and DELETE /api/sessions/current
# ---------------------------------------------------------------------------


def test_connect_session_returns_200(client, tmp_path):
    import coordinator.state as state_mod

    s = state_mod.empty_state()
    s["sessions"]["work"] = {"bell": state_mod.empty_bell()}
    s["session_order"] = ["work"]
    state_mod.save_state(s)

    mock_proc = MagicMock()
    mock_proc.pid = 12345

    with patch("coordinator.ttyd.kill_ttyd", new_callable=AsyncMock), \
         patch("coordinator.ttyd.spawn_ttyd", new_callable=AsyncMock,
               return_value=mock_proc):
        response = client.post("/api/sessions/work/connect")

    assert response.status_code == 200


def test_connect_session_sets_active_session(client, tmp_path):
    import coordinator.state as state_mod

    s = state_mod.empty_state()
    s["sessions"]["work"] = {"bell": state_mod.empty_bell()}
    s["session_order"] = ["work"]
    state_mod.save_state(s)

    mock_proc = MagicMock()
    mock_proc.pid = 12345

    with patch("coordinator.ttyd.kill_ttyd", new_callable=AsyncMock), \
         patch("coordinator.ttyd.spawn_ttyd", new_callable=AsyncMock,
               return_value=mock_proc):
        client.post("/api/sessions/work/connect")

    check = client.get("/api/state")
    assert check.json()["active_session"] == "work"


def test_connect_session_kills_existing_ttyd(client, tmp_path):
    import coordinator.state as state_mod

    s = state_mod.empty_state()
    s["sessions"]["work"] = {"bell": state_mod.empty_bell()}
    s["session_order"] = ["work"]
    state_mod.save_state(s)

    mock_proc = MagicMock()
    mock_proc.pid = 1

    with patch("coordinator.ttyd.kill_ttyd", new_callable=AsyncMock) as mock_kill, \
         patch("coordinator.ttyd.spawn_ttyd", new_callable=AsyncMock,
               return_value=mock_proc):
        client.post("/api/sessions/work/connect")

    mock_kill.assert_called_once()


def test_connect_nonexistent_session_returns_404(client, tmp_path):
    import coordinator.state as state_mod

    s = state_mod.empty_state()
    state_mod.save_state(s)

    with patch("coordinator.sessions.get_session_list", return_value=["main"]):
        response = client.post("/api/sessions/ghost/connect")

    assert response.status_code == 404


def test_delete_current_kills_ttyd_and_clears_active(client, tmp_path):
    import coordinator.state as state_mod

    s = state_mod.empty_state()
    s["active_session"] = "work"
    state_mod.save_state(s)

    with patch("coordinator.ttyd.kill_ttyd", new_callable=AsyncMock) as mock_kill:
        response = client.delete("/api/sessions/current")

    assert response.status_code == 200
    mock_kill.assert_called_once()

    check = client.get("/api/state")
    assert check.json()["active_session"] is None
```

**Step 2: Run to verify failures**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_api.py::test_connect_session_returns_200 -v
```

Expected: `FAILED` — 404 Not Found (endpoint missing)

**Step 3: Add connect and delete endpoints to `coordinator/main.py`**

Append after the `get_sessions` endpoint in `/home/bkrabach/dev/web-tmux/coordinator/main.py`:

```python
@app.post("/api/sessions/{name}/connect")
async def connect_session(name: str) -> dict:
    """
    Kill any existing ttyd, spawn a fresh one for the named session,
    and update active_session in state.

    Returns 404 if the session is not in the current session list.
    """
    known = sessions.get_session_list()
    if known and name not in known:
        raise HTTPException(status_code=404, detail=f"Session '{name}' not found")

    await ttyd.kill_ttyd()
    await ttyd.spawn_ttyd(name)

    async with state.state_lock:
        current = state.load_state()
        current["active_session"] = name
        state.save_state(current)

    return {"active_session": name, "ttyd_port": ttyd.TTYD_PORT}


@app.delete("/api/sessions/current")
async def disconnect_session() -> dict:
    """
    Kill the active ttyd process and clear active_session from state.
    """
    await ttyd.kill_ttyd()

    async with state.state_lock:
        current = state.load_state()
        current["active_session"] = None
        state.save_state(current)

    return {"active_session": None}
```

**Step 4: Run tests**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_api.py -v -k "connect or delete"
```

Expected: all 5 tests pass.

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux
git add coordinator/main.py coordinator/tests/test_api.py
git commit -m "feat: main.py — connect/disconnect session endpoints"
```

---

## Task 16: main.py — POST /api/heartbeat

**Files:**
- Modify: `coordinator/main.py`
- Modify: `coordinator/tests/test_api.py`

**Step 1: Add failing tests**

Append to `/home/bkrabach/dev/web-tmux/coordinator/tests/test_api.py`:

```python
# ---------------------------------------------------------------------------
# POST /api/heartbeat
# ---------------------------------------------------------------------------

import time


def test_heartbeat_returns_200(client):
    response = client.post(
        "/api/heartbeat",
        json={
            "device_id": "d-abc123",
            "label": "Laptop Chrome",
            "viewing_session": None,
            "view_mode": "grid",
            "last_interaction_at": time.time(),
        },
    )
    assert response.status_code == 200


def test_heartbeat_registers_new_device(client):
    now = time.time()
    client.post(
        "/api/heartbeat",
        json={
            "device_id": "d-new",
            "label": "Test Phone",
            "viewing_session": "work",
            "view_mode": "fullscreen",
            "last_interaction_at": now,
        },
    )
    check = client.get("/api/state")
    devices = check.json()["devices"]
    assert "d-new" in devices
    assert devices["d-new"]["label"] == "Test Phone"
    assert devices["d-new"]["viewing_session"] == "work"


def test_heartbeat_updates_existing_device(client):
    now = time.time()

    # First heartbeat
    client.post(
        "/api/heartbeat",
        json={
            "device_id": "d-abc",
            "label": "Laptop",
            "viewing_session": None,
            "view_mode": "grid",
            "last_interaction_at": now - 10,
        },
    )

    # Second heartbeat with updated view state
    client.post(
        "/api/heartbeat",
        json={
            "device_id": "d-abc",
            "label": "Laptop",
            "viewing_session": "dev-server",
            "view_mode": "fullscreen",
            "last_interaction_at": now,
        },
    )

    check = client.get("/api/state")
    device = check.json()["devices"]["d-abc"]
    assert device["viewing_session"] == "dev-server"
    assert device["view_mode"] == "fullscreen"


def test_heartbeat_missing_device_id_returns_422(client):
    response = client.post(
        "/api/heartbeat",
        json={
            "label": "Laptop",
            "viewing_session": None,
            "view_mode": "grid",
            "last_interaction_at": time.time(),
        },
    )
    assert response.status_code == 422


def test_heartbeat_invalid_view_mode_returns_422(client):
    response = client.post(
        "/api/heartbeat",
        json={
            "device_id": "d-abc",
            "label": "Laptop",
            "viewing_session": None,
            "view_mode": "invalid-mode",   # must be "grid" or "fullscreen"
            "last_interaction_at": time.time(),
        },
    )
    assert response.status_code == 422
```

**Step 2: Run to verify failures**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_api.py::test_heartbeat_returns_200 -v
```

Expected: `FAILED` — 404 Not Found

**Step 3: Add heartbeat endpoint to `coordinator/main.py`**

Add this import and endpoint in `/home/bkrabach/dev/web-tmux/coordinator/main.py`.

First, add `Literal` to the imports at the top:
```python
from typing import Literal
```

Then append after the `disconnect_session` endpoint:

```python
class HeartbeatPayload(BaseModel):
    device_id: str
    label: str
    viewing_session: str | None
    view_mode: Literal["grid", "fullscreen"]
    last_interaction_at: float


@app.post("/api/heartbeat")
async def heartbeat(payload: HeartbeatPayload) -> dict:
    """
    Receive a device heartbeat.

    Called by the dashboard every 5 seconds. Updates device registration,
    view state, and interaction timestamp in state.json.
    """
    async with state.state_lock:
        current = state.load_state()
        state.register_device(
            current,
            device_id=payload.device_id,
            label=payload.label,
            viewing_session=payload.viewing_session,
            view_mode=payload.view_mode,
            last_interaction_at=payload.last_interaction_at,
        )
        state.save_state(current)
    return {"device_id": payload.device_id, "status": "ok"}
```

**Step 4: Run all tests**

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_api.py -v
```

Expected: all API tests pass (approximately 25 tests).

**Step 5: Run full test suite**

```bash
cd /home/bkrabach/dev/web-tmux
pytest -v
```

Expected: all tests pass across all test files.

**Step 6: Commit**

```bash
cd /home/bkrabach/dev/web-tmux
git add coordinator/main.py coordinator/tests/test_api.py
git commit -m "feat: main.py — POST /api/heartbeat endpoint"
```

---

## Task 17: Integration Test — Real tmux + Full Poll Cycle

**Files:**
- Create: `coordinator/tests/test_integration.py`

**⚠️ Requirements:** tmux must be installed on the host. These tests are marked `integration` and are excluded from the default pytest run. Run them explicitly.

**Step 1: Write the integration test**

Create `/home/bkrabach/dev/web-tmux/coordinator/tests/test_integration.py`:

```python
"""
Integration tests — require tmux installed on the host.

These tests spin up a real, isolated tmux server on a test socket and verify
the full coordinator poll cycle end-to-end. They do NOT require a browser.

Run with:
    pytest coordinator/tests/test_integration.py -v -m integration

Skip with:
    pytest -m "not integration"   (default run excludes these)
"""
import asyncio
import subprocess
import time

import pytest


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tmux_server():
    """
    Start an isolated tmux server on socket 'test-server'.
    Creates one session named 'test' (220x50).
    Tears down after all tests in this module.
    """
    socket = "test-server"
    subprocess.run(
        ["tmux", "-L", socket, "new-session", "-d", "-s", "test", "-x", "220", "-y", "50"],
        check=True,
        capture_output=True,
    )
    yield socket
    subprocess.run(
        ["tmux", "-L", socket, "kill-server"],
        capture_output=True,
    )


@pytest.fixture(autouse=True)
def use_tmp_state(tmp_path, monkeypatch):
    """Redirect all state files to tmp_path for each test."""
    import coordinator.state as state_mod
    import coordinator.ttyd as ttyd_mod

    monkeypatch.setattr(state_mod, "STATE_DIR", tmp_path)
    monkeypatch.setattr(state_mod, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(ttyd_mod, "TTYD_PID_DIR", tmp_path)
    monkeypatch.setattr(ttyd_mod, "TTYD_PID_PATH", tmp_path / "ttyd.pid")


# ---------------------------------------------------------------------------
# Helper: run tmux command against the test socket
# ---------------------------------------------------------------------------


def tmux(socket: str, *args: str) -> str:
    result = subprocess.run(
        ["tmux", "-L", socket, *args],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_enumerate_sessions_finds_test_session(tmux_server):
    from coordinator.sessions import enumerate_sessions

    # Temporarily make run_tmux use the test socket
    import coordinator.sessions as sessions_mod

    original = sessions_mod.run_tmux

    async def patched_run_tmux(*args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "tmux", "-L", tmux_server, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode())
        return stdout.decode()

    sessions_mod.run_tmux = patched_run_tmux
    try:
        result = await enumerate_sessions()
    finally:
        sessions_mod.run_tmux = original

    assert "test" in result


async def test_capture_pane_returns_content(tmux_server):
    """Send text to tmux, capture it, verify it appears in snapshot."""
    import coordinator.sessions as sessions_mod

    socket = tmux_server

    # Send some text to the test session
    subprocess.run(
        ["tmux", "-L", socket, "send-keys", "-t", "test", "echo hello-world", "Enter"],
        check=True,
    )
    time.sleep(0.3)  # wait for output to appear

    async def patched_run_tmux(*args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "tmux", "-L", socket, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode()

    original = sessions_mod.run_tmux
    sessions_mod.run_tmux = patched_run_tmux
    try:
        from coordinator.sessions import capture_pane
        output = await capture_pane("test")
    finally:
        sessions_mod.run_tmux = original

    assert "hello-world" in output


async def test_bell_flag_detected_after_printf_bell(tmux_server):
    """
    Send a bell to the test session via printf '\a'.
    Verify that poll_bell_flag returns True.
    """
    import coordinator.bells as bells_mod
    import coordinator.sessions as sessions_mod

    socket = tmux_server

    async def patched_run_tmux(*args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "tmux", "-L", socket, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode()

    original = sessions_mod.run_tmux
    sessions_mod.run_tmux = patched_run_tmux
    bells_mod.run_tmux = patched_run_tmux

    try:
        # Trigger a bell
        subprocess.run(
            ["tmux", "-L", socket, "send-keys", "-t", "test",
             "printf '\\a'", "Enter"],
            check=True,
        )
        time.sleep(0.3)

        from coordinator.bells import poll_bell_flag
        result = await poll_bell_flag("test")
    finally:
        sessions_mod.run_tmux = original
        bells_mod.run_tmux = original

    assert result is True, (
        "Bell flag should be 1 after printf '\\a'. "
        "If this fails, re-run the spike script to verify bell behavior."
    )


async def test_full_poll_cycle_via_api(tmux_server, tmp_path):
    """
    Run a full poll cycle through the FastAPI app (via TestClient).

    Verifies: session appears in /api/sessions after poll cycle runs.
    """
    import coordinator.sessions as sessions_mod
    from unittest.mock import patch, AsyncMock

    socket = tmux_server

    async def patched_run_tmux(*args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "tmux", "-L", socket, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode()

    sessions_mod.run_tmux = patched_run_tmux

    try:
        from coordinator.main import _run_poll_cycle
        await _run_poll_cycle()
    finally:
        # Restore: import original run_tmux
        import importlib
        importlib.reload(sessions_mod)

    # Verify state was written with the session
    from coordinator.state import load_state
    s = load_state()
    assert "test" in s["session_order"]


async def test_state_file_written_atomically_by_poll_cycle(tmux_server, tmp_path):
    """
    After a poll cycle, state.json must exist and be valid JSON.
    The .tmp file must NOT exist (os.replace was called).
    """
    import coordinator.sessions as sessions_mod
    import coordinator.state as state_mod

    socket = tmux_server

    async def patched_run_tmux(*args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "tmux", "-L", socket, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode()

    sessions_mod.run_tmux = patched_run_tmux

    try:
        from coordinator.main import _run_poll_cycle
        await _run_poll_cycle()
    finally:
        import importlib
        importlib.reload(sessions_mod)

    state_path = state_mod.STATE_PATH
    tmp_path_file = state_path.with_suffix(".tmp")

    assert state_path.exists(), "state.json must exist after poll cycle"
    assert not tmp_path_file.exists(), ".tmp file must be cleaned up (os.replace)"

    # Verify it's valid JSON
    import json
    content = json.loads(state_path.read_text())
    assert "active_session" in content
    assert "session_order" in content
```

**Step 2: Add the `integration` mark to `pyproject.toml`**

Append to the `[tool.pytest.ini_options]` section in `/home/bkrabach/dev/web-tmux/pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["coordinator/tests"]
asyncio_mode = "auto"
addopts = "--import-mode=importlib -m 'not integration'"
markers = [
    "integration: marks tests that require tmux installed on the host",
]
```

> **Note:** The `addopts` line above adds `-m 'not integration'` so integration tests are excluded from the default `pytest` run. Run integration tests explicitly with `pytest -m integration`.

**Step 3: Verify unit tests still all pass**

```bash
cd /home/bkrabach/dev/web-tmux
pytest -v
```

Expected: all unit tests pass, integration tests are skipped (not collected due to `-m 'not integration'`).

**Step 4: Run the integration tests** (requires tmux)

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_integration.py -v -m integration
```

Expected:
```
PASSED ...test_enumerate_sessions_finds_test_session
PASSED ...test_capture_pane_returns_content
PASSED ...test_bell_flag_detected_after_printf_bell
PASSED ...test_full_poll_cycle_via_api
PASSED ...test_state_file_written_atomically_by_poll_cycle
5 passed in X.XXs
```

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux
git add coordinator/tests/test_integration.py pyproject.toml
git commit -m "test: integration tests — real tmux server, full poll cycle end-to-end"
```

---

## Final Verification

**Run all unit tests:**

```bash
cd /home/bkrabach/dev/web-tmux
pytest -v
```

Expected: all tests pass with zero failures.

**Run integration tests** (if tmux is available):

```bash
cd /home/bkrabach/dev/web-tmux
pytest coordinator/tests/test_integration.py -v -m integration
```

**Smoke test — start the server manually:**

```bash
cd /home/bkrabach/dev/web-tmux
python3 -m uvicorn coordinator.main:app --host 0.0.0.0 --port 8080 --reload
```

Then in another terminal:
```bash
curl -s http://localhost:8080/health | python3 -m json.tool
# Expected: {"status": "ok"}

curl -s http://localhost:8080/api/state | python3 -m json.tool
# Expected: full state JSON with active_session: null

curl -s http://localhost:8080/api/sessions | python3 -m json.tool
# Expected: list of running tmux sessions with snapshots
```

**Final commit (if any loose ends):**

```bash
cd /home/bkrabach/dev/web-tmux
git status  # should be clean
git log --oneline -20
```

---

## Phase 1 Completion Checklist

- [ ] Task 0: Bell flag spike script created and run, findings documented in `bells.py`
- [ ] Task 1: `requirements.txt`, `pyproject.toml`, directory skeleton committed
- [ ] Task 2: `state.py` — schema factories, all tests green
- [ ] Task 3: `state.py` — atomic read/write with lock, all tests green
- [ ] Task 4: `state.py` — device registration + pruning, all tests green
- [ ] Task 5: `sessions.py` — session enumeration + `run_tmux`, all tests green
- [ ] Task 6: `sessions.py` — capture-pane tests added, all tests green
- [ ] Task 7: `bells.py` — bell polling + unseen_count, all tests green
- [ ] Task 8: `bells.py` — active-device gate rule, all tests green
- [ ] Task 9: `ttyd.py` — spawn + PID file, all tests green
- [ ] Task 10: `ttyd.py` — kill + cleanup, all tests green
- [ ] Task 11: `ttyd.py` — orphan detection, all tests green
- [ ] Task 12: `main.py` — skeleton + lifespan + `/health`, all tests green
- [ ] Task 13: `main.py` — `GET/PATCH /api/state`, all tests green
- [ ] Task 14: `main.py` — `GET /api/sessions`, all tests green
- [ ] Task 15: `main.py` — connect/disconnect endpoints, all tests green
- [ ] Task 16: `main.py` — `POST /api/heartbeat`, all tests green
- [ ] Task 17: Integration tests written and passing

**Ready for Phase 2 (Frontend)** when all items above are checked.

---

## Scope Boundary Reminder

Phase 1 does **not** include:
- Any HTML, CSS, or JavaScript files
- xterm.js integration
- Caddy configuration
- systemd service file
- PWA manifest
- Mobile layout or responsive breakpoints
- Bell visual indicators (amber dot, pulsing animation)
- Browser Notifications API
- The backtick key handler (noted in code comments for Phase 2)

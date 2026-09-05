"""
The stale-key prune's destructive-write backstop, driven through the REAL
poll cycle (`main._run_poll_cycle`, step 14) -- not through a hand-rolled
mirror of it.

WHY THIS FILE EXISTS -- and why an isolated test was not enough:

`test_views.py::test_mass_prune_that_would_collapse_views_is_refused_by_backstop`
already asserts the backstop's arithmetic, but it builds its own before-snapshot
(`[dict(v, sessions=list(v["sessions"])) for v in views_before]`) and calls
`assess_views_destruction` itself. That test passed for the entire lifetime of
the bug it was meant to guard: the production call site took

    _views_before_prune = _prune_settings.get("views")

-- a REFERENCE to the live list of live view dicts -- and `prune_stale_keys`
then removed members by rebinding `view["sessions"] = [...]` on those very
dicts. By the time `assess_views_destruction(_views_before_prune,
_prune_settings.get("views"))` ran, both arguments were the same objects in the
same post-prune state, so `before_members == after_members` always and all three
thresholds were unreachable. The backstop on the automatic prune path was dead
code, and the isolated test could not see it, because the aliasing lives in the
caller, not in either function.

So every test here drives `_run_poll_cycle()` itself and asserts on what is
actually on disk afterwards. Nothing here re-implements the poll cycle's
snapshot, its live-key assembly, or its persist decision -- that reconstruction
is exactly what hid the bug.
"""

from __future__ import annotations

import copy
import time

import pytest

import muxplex.main as main_mod
from muxplex.pruning import load_pruning_state, save_pruning_state
from muxplex.settings import load_settings, save_settings

LOCAL_DEVICE = "dev-local"
REMOTE_DEVICE = "dev-remote"

# settings["stale_key_grace_hours"] defaults to 24h; anything older than that
# is past grace and eligible for pruning.
_PAST_GRACE = 25 * 3600.0


# This module used to redirect PRUNING_STATE_PATH / STATE_PATH / MANIFEST_PATH
# itself, because conftest.py isolated SETTINGS_PATH alone. conftest.py now
# isolates all three (plus STATE_DIR) for EVERY test, so the local copy is
# gone -- its whole point, that "this file asserts specific files were NOT
# written, which is meaningless if they point at the developer's real ones",
# is now a property of the suite rather than of this file.
#
# Worth knowing why the local copy was not enough even here: it redirected
# STATE_PATH but not STATE_DIR, and `save_state()` mkdirs STATE_DIR before
# writing -- so a divert-probe still caught these four tests reaching into
# the real ~/.local/share/muxplex. conftest's `_isolate_state_path` patches
# both.


@pytest.fixture(autouse=True)
def _mock_poll_dependencies(monkeypatch):
    """Make `_run_poll_cycle()` run deterministically with no real tmux.

    Mirrors test_api.py's `_mock_poll_dependencies`. `names` is empty: these
    tests are about REMOTE-owned pins whose owning device is reachable and
    reports zero sessions, which is the real-world shape of a catastrophic
    prune (a peer that came back up with its tmux server wiped).
    """
    from unittest.mock import AsyncMock

    async def mock_enumerate():
        return []

    async def mock_snapshot_all(_names):
        return {}

    monkeypatch.setattr(main_mod, "enumerate_sessions", mock_enumerate)
    monkeypatch.setattr(main_mod, "snapshot_all", mock_snapshot_all)
    monkeypatch.setattr(main_mod, "get_session_created_times", dict)
    monkeypatch.setattr(main_mod, "update_session_cache", lambda names, snaps: None)
    monkeypatch.setattr(main_mod, "process_bell_flags", AsyncMock())
    monkeypatch.setattr(main_mod, "apply_bell_clear_rule", lambda state: None)
    monkeypatch.setattr(main_mod, "prune_devices", lambda state: None)
    monkeypatch.setattr(main_mod, "load_device_id", lambda: LOCAL_DEVICE)
    monkeypatch.setattr(main_mod, "_bell_hook_armed", True)
    monkeypatch.setattr(main_mod, "_bell_hook_last_error", None)
    # The peer is REACHABLE (fail_count 0) and reports zero live sessions --
    # the positive-knowledge rule therefore makes its keys evaluable, which is
    # what lets the prune fire at all.
    monkeypatch.setattr(
        main_mod,
        "_federation_cache",
        {REMOTE_DEVICE: {"fail_count": 0, "sessions": []}},
    )


def _seed(view_count: int, pins_per_view: int) -> tuple[dict, dict]:
    """Persist `view_count` views of `pins_per_view` remote pins, all past grace.

    Returns the (settings, pruning_state) as written, for later comparison.
    """
    views = [
        {
            "name": f"view-{i}",
            "sessions": [f"{REMOTE_DEVICE}:dead-{i}-{j}" for j in range(pins_per_view)],
        }
        for i in range(view_count)
    ]
    settings = load_settings()
    settings["views"] = views
    settings["hidden_sessions"] = []
    save_settings(settings)

    first_missed_at = {
        key: time.time() - _PAST_GRACE for v in views for key in v["sessions"]
    }
    pruning_state = {"first_missed_at": first_missed_at}
    save_pruning_state(pruning_state)

    return copy.deepcopy(load_settings()), copy.deepcopy(pruning_state)


async def test_poll_cycle_refuses_prune_that_would_lose_half_the_view_members():
    """8 views x 2 pins, every pin dead past grace: a 16 -> 0 member wipe.

    That is a 100% member drop against DESTRUCTIVE_MEMBER_DROP_RATIO's 50%
    threshold, so step 14's backstop must refuse it and persist NOTHING.

    Against the aliased-snapshot bug this fails on the very first assertion:
    the prune is applied and saved, and every view comes back empty.
    """
    settings_before, _ = _seed(view_count=8, pins_per_view=2)

    await main_mod._run_poll_cycle()

    assert load_settings()["views"] == settings_before["views"], (
        "the poll cycle persisted a prune that wipes 100% of view membership -- "
        "the destructive-write backstop did not fire"
    )


async def test_poll_cycle_refused_prune_also_leaves_pruning_state_untouched():
    """A refused prune must persist NOTHING -- pruning.json included.

    Leaving first_missed_at alone is what makes the refusal reproduce (and
    keep logging) on the next cycle instead of silently landing in a
    half-written state where settings were spared but the clocks were reset.
    """
    _, pruning_before = _seed(view_count=8, pins_per_view=2)

    await main_mod._run_poll_cycle()

    assert load_pruning_state() == pruning_before, (
        "a refused prune rewrote pruning.json -- the refusal must persist "
        "nothing at all, or the next cycle sees different bookkeeping"
    )


async def test_poll_cycle_refuses_prune_that_would_collapse_views_to_one():
    """The collapse threshold, reached the only way a prune can reach it.

    A prune never removes a whole view -- it empties `sessions` -- so
    before_views == after_views on this path and the collapse rule cannot
    fire from view count alone. What CAN vanish is membership, and this is
    the shape a real user notices: many views, each holding exactly one pin,
    all of them dead. Refusing it is the difference between "one peer went
    away" and "my sidebar is empty".
    """
    settings_before, _ = _seed(view_count=6, pins_per_view=1)

    await main_mod._run_poll_cycle()

    after = load_settings()["views"]
    assert after == settings_before["views"], (
        "a 6-view, one-pin-each wipe was persisted -- backstop did not fire"
    )
    assert all(v["sessions"] for v in after), "every view lost its only pin"


async def test_poll_cycle_still_applies_a_small_non_destructive_prune():
    """The backstop must not become a blanket refusal.

    One dead pin out of eight is a 12.5% member drop -- well under every
    threshold -- so it must still be pruned and persisted, and its
    bookkeeping entry dropped. Without this, a fix that simply stopped
    pruning would pass the three tests above.
    """
    views = [
        {
            "name": "keep",
            "sessions": [f"{REMOTE_DEVICE}:alive-{j}" for j in range(7)]
            + [f"{REMOTE_DEVICE}:dead-1"],
        }
    ]
    settings = load_settings()
    settings["views"] = views
    settings["hidden_sessions"] = []
    save_settings(settings)
    save_pruning_state(
        {"first_missed_at": {f"{REMOTE_DEVICE}:dead-1": time.time() - _PAST_GRACE}}
    )

    # The peer is reachable and reports the seven survivors as live.
    main_mod._federation_cache[REMOTE_DEVICE]["sessions"] = [
        {"sessionKey": f"{REMOTE_DEVICE}:alive-{j}"} for j in range(7)
    ]

    await main_mod._run_poll_cycle()

    sessions = load_settings()["views"][0]["sessions"]
    assert f"{REMOTE_DEVICE}:dead-1" not in sessions, (
        "a small, safe prune was refused -- the backstop is over-firing"
    )
    assert len(sessions) == 7, "a safe prune removed more than the one dead pin"
    assert f"{REMOTE_DEVICE}:dead-1" not in load_pruning_state().get(
        "first_missed_at", {}
    ), "an applied prune must drop the pruned key's bookkeeping entry"

"""Consumer-boundary regressions for tmux-kit 0.6.0 reliability APIs."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock

import pytest

import muxplex.main as main_mod
import muxplex.manifest as manifest_mod
import muxplex.restore as restore_mod
import muxplex.state as state_mod
from muxplex.manifest import (
    load_manifest,
    manifest_write_lock,
    save_manifest,
    update_manifest,
)


def _quiet_poll_dependencies(monkeypatch) -> None:
    """Replace the non-inventory poll work with no-ops for boundary tests."""

    async def snapshot_all(_names: list[str]) -> dict[str, str]:
        return {}

    async def no_epoch() -> None:
        return None

    monkeypatch.setattr(main_mod, "snapshot_all", snapshot_all)
    monkeypatch.setattr(main_mod, "probe_tmux_epoch", no_epoch)
    monkeypatch.setattr(main_mod, "update_session_cache", lambda *_: None)
    monkeypatch.setattr(main_mod, "process_bell_flags", AsyncMock())
    monkeypatch.setattr(main_mod, "apply_bell_clear_rule", lambda _: None)
    monkeypatch.setattr(main_mod, "prune_devices", lambda _: None)
    monkeypatch.setattr(main_mod, "_bell_hook_armed", True)


async def test_poll_failed_strict_inventory_retains_state_and_followups(monkeypatch):
    """Unavailable tmux is not evidence that every session disappeared."""
    _quiet_poll_dependencies(monkeypatch)
    original = {
        "session_order": ["kept"],
        "sessions": {"kept": {"bell": {"unseen_count": 1}}},
        "terminal_session": "kept",
        "followups": {"kept": {"revision": 1, "items": [{"id": "x", "text": "keep"}]}},
    }
    state_mod.save_state(original)
    cache_updates: list[object] = []

    async def unavailable() -> list[str]:
        raise RuntimeError("tmux temporarily unavailable")

    monkeypatch.setattr(main_mod, "enumerate_sessions_strict", unavailable)
    monkeypatch.setattr(
        main_mod, "update_session_cache", lambda *args: cache_updates.append(args)
    )

    await main_mod._run_poll_cycle()

    state = state_mod.load_state()
    assert state["session_order"] == ["kept"]
    assert state["sessions"]["kept"]["bell"]["unseen_count"] == 1
    assert state["terminal_session"] == "kept"
    assert state["followups"]["kept"]["items"][0]["text"] == "keep"
    assert cache_updates == []


async def test_poll_confirmed_empty_inventory_reconciles_but_retains_followups(
    monkeypatch,
):
    """A successful empty inventory still performs normal absence reconciliation."""
    _quiet_poll_dependencies(monkeypatch)
    state_mod.save_state(
        {
            "session_order": ["gone"],
            "sessions": {"gone": {"bell": {"unseen_count": 1}}},
            "active_session": "gone",
            "terminal_session": "gone",
            "devices": {},
            "followups": {
                "gone": {"revision": 1, "items": [{"id": "x", "text": "keep"}]}
            },
        }
    )

    async def empty() -> list[str]:
        return []

    monkeypatch.setattr(main_mod, "enumerate_sessions_strict", empty)

    await main_mod._run_poll_cycle()

    state = state_mod.load_state()
    assert state["sessions"] == {}
    assert state["session_order"] == []
    assert state["active_session"] is None
    assert state["terminal_session"] is None
    assert state["followups"]["gone"]["items"][0]["text"] == "keep"


async def test_restore_strict_verification_failure_keeps_name_pending(monkeypatch):
    """A post-spawn observation outage is distinct from a confirmed absence."""
    save_manifest(
        {
            "schema": 2,
            "epoch": None,
            "sessions": {},
            "pending_restore": {
                "detected_at": 1.0,
                "lost_epoch": {},
                "sessions": {"unverified": {}},
            },
            "created_with": {},
            "rename_in_flight": None,
        }
    )

    async def spawned(*_args, **_kwargs) -> tuple[bool, None]:
        return True, None

    async def unavailable() -> list[str]:
        raise RuntimeError("tmux disconnected during verification")

    monkeypatch.setattr(restore_mod, "spawn_session_command", spawned)
    monkeypatch.setattr(restore_mod, "enumerate_sessions_strict", unavailable)

    report = await restore_mod.execute_restore(["unverified"], force=True)

    assert report.results[0].status == "fail"
    assert "could not verify" in report.results[0].detail
    assert "unverified" in load_manifest()["pending_restore"]["sessions"]


async def test_restore_persists_verified_progress_before_reporter_failure(monkeypatch):
    """A reporting failure cannot make retry recreate an already verified session."""
    save_manifest(
        {
            "schema": 2,
            "epoch": None,
            "sessions": {},
            "pending_restore": {
                "detected_at": 1.0,
                "lost_epoch": {},
                "sessions": {"first": {}, "second": {}},
            },
            "created_with": {},
            "rename_in_flight": None,
        }
    )
    spawned: list[str] = []

    async def spawn(name: str, **_kwargs) -> tuple[bool, None]:
        spawned.append(name)
        return True, None

    async def observed() -> list[str]:
        return ["first"]

    async def no_window_probe(_name: str) -> None:
        return None

    class ReporterFailed(RuntimeError):
        pass

    def fail_to_report(_result):
        raise ReporterFailed("reporter stopped")

    monkeypatch.setattr(restore_mod, "spawn_session_command", spawn)
    monkeypatch.setattr(restore_mod, "enumerate_sessions_strict", observed)
    monkeypatch.setattr(restore_mod, "_probe_windows", no_window_probe)
    with pytest.raises(ReporterFailed, match="reporter stopped"):
        await restore_mod.execute_restore(
            ["first", "second"], force=True, on_result=fail_to_report
        )

    assert spawned == ["first"]
    assert set(load_manifest()["pending_restore"]["sessions"]) == {"second"}


def test_manifest_lock_prevents_stale_poll_from_restoring_cleared_pending_name():
    """A paused poll RMW cannot overwrite restore's completed progress."""
    manifest_path = manifest_mod.MANIFEST_PATH
    save_manifest(
        {
            "schema": 2,
            "epoch": {"server_pid": 9, "inode": 9, "socket_path": "socket"},
            "sessions": {"live": {}},
            "pending_restore": {
                "detected_at": 1.0,
                "lost_epoch": {},
                "sessions": {"restored": {}, "still-pending": {}},
            },
            "created_with": {},
            "rename_in_flight": None,
        }
    )
    entered = threading.Event()
    release = threading.Event()
    restore_done = threading.Event()
    errors: list[BaseException] = []

    def stale_poll_writer() -> None:
        try:
            with manifest_write_lock():
                stale = load_manifest()
                entered.set()
                assert release.wait(timeout=2), "test did not release stale poll"
                updated, _ = update_manifest(
                    stale,
                    {"server_pid": 9, "inode": 9, "socket_path": "socket"},
                    ["live"],
                )
                save_manifest(updated)
        except BaseException as exc:  # noqa: BLE001 - assert after both workers join
            errors.append(exc)

    def restore_writer() -> None:
        try:
            asyncio.run(restore_mod._persist_restored({"restored"}))
            restore_done.set()
        except BaseException as exc:  # noqa: BLE001 - assert after both workers join
            errors.append(exc)

    poll = threading.Thread(target=stale_poll_writer)
    poll.start()
    assert entered.wait(timeout=2)
    restore = threading.Thread(target=restore_writer)
    restore.start()
    assert not restore_done.wait(timeout=0.1), (
        "restore bypassed the manifest RMW lock while poll held a stale snapshot"
    )
    release.set()
    poll.join(timeout=2)
    restore.join(timeout=2)

    assert not poll.is_alive()
    assert not restore.is_alive()
    assert errors == []
    assert manifest_path.exists()
    assert set(load_manifest()["pending_restore"]["sessions"]) == {"still-pending"}
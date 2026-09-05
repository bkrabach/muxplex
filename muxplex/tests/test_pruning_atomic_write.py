"""Durability guarantees for ``pruning.json`` -- the stale-key grace clock.

``pruning.json`` holds ``first_missed_at``: the per-device timestamp at which
each session key was first observed missing. That clock is the ONLY input to
"has this key been gone long enough to prune?", so losing it does not merely
lose bookkeeping -- it restarts the grace period for every session at once and
silently changes WHEN real view pins get removed.

Losing it was cheap. ``save_pruning_state()`` ended in a bare
``PRUNING_STATE_PATH.write_text(...)``: no temp file, no ``os.replace()``, no
fsync. An interrupted write (crash, OOM, power cut, full disk) left a truncated
JSON file, and ``load_pruning_state()`` swallows ``JSONDecodeError`` and returns
``{}`` -- so the damage is not just silent, it *erases its own evidence*. The
next poll cycle rewrites a clean, complete, wrong file.

This is the fourth and last of muxplex's state files to get the tmp + fsync +
``os.replace()`` treatment (settings.json, sessions.json, state.json preceded
it), and it does not carry a fifth copy of the implementation: it delegates to
``settings.atomic_write_text``.

**How "interrupted" is simulated here.** The same way
``test_settings_atomic_write.py`` does it, and for the same reason: an in-process
test cannot actually be killed mid-``write()``, but it can prove the stronger
structural property that makes a truncation impossible -- the target file is
only ever modified by the final ``os.replace()``. Stub that one call out and a
complete ``save_pruning_state()`` must leave the target byte-for-byte unchanged.
If nothing but an atomic rename can touch the file, no interruption can leave it
half-written.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import muxplex.pruning as pruning_mod
import muxplex.settings as settings_mod
from muxplex.pruning import load_pruning_state, save_pruning_state

# ``settings_mod.os`` IS the stdlib ``os`` module, so patching ``replace`` /
# ``fsync`` through it reaches whichever module performs the call. These tests
# pin the *property* (the sidecar is published atomically), not the call site --
# they stay honest whether the write lives in pruning.py or in the shared helper
# it currently delegates to.

GRACE_CLOCK = {"first_missed_at": {"dev1:dead-session": 1747512345.0}}


@pytest.fixture
def pruning_sidecar(_isolate_pruning_state_path):
    """The isolated sidecar path, with its parent directory created.

    conftest's autouse rail redirects ``PRUNING_STATE_PATH`` for every test but
    deliberately does not create the directory (a fresh host has none, and
    creating it is itself a tested behaviour). Tests here plant and inspect file
    contents directly, so they need the parent to exist.
    """
    _isolate_pruning_state_path.parent.mkdir(parents=True, exist_ok=True)
    return _isolate_pruning_state_path


# ---------------------------------------------------------------------------
# The headline property: the sidecar is never written in place
# ---------------------------------------------------------------------------


def test_save_pruning_state_publishes_via_os_replace_not_in_place(
    pruning_sidecar, monkeypatch
):
    """With ``os.replace`` stubbed to record-and-do-nothing, a full
    ``save_pruning_state()`` must leave the target BYTE-FOR-BYTE unchanged.

    A bare ``write_text()`` implementation fails this immediately: the target
    changes even though the publish step never ran, which is the same thing as
    saying an interruption can catch it half-written.
    """
    save_pruning_state(GRACE_CLOCK)
    original_bytes = pruning_sidecar.read_bytes()

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        settings_mod.os, "replace", lambda src, dst: calls.append((str(src), str(dst)))
    )

    save_pruning_state({"first_missed_at": {"dev1:dead-session": 9999999999.0}})

    assert calls, "save_pruning_state() must publish via os.replace()"
    assert calls[-1][1] == str(pruning_sidecar)
    assert pruning_sidecar.read_bytes() == original_bytes, (
        "the sidecar was modified without going through os.replace() -- "
        "an interrupted write can therefore truncate it"
    )


def test_an_interrupted_write_never_degrades_the_grace_clock_to_empty(
    pruning_sidecar, monkeypatch
):
    """The user-visible consequence, stated end to end.

    A write that dies at the publish step must leave the previous
    ``first_missed_at`` complete and readable. The failure being guarded is not
    "the file is odd" -- it is ``load_pruning_state()`` returning ``{}``, which
    restarts the grace period for every session key with no error anywhere.
    """
    save_pruning_state(GRACE_CLOCK)

    def _boom(src, dst):
        raise OSError("simulated crash during publish")

    monkeypatch.setattr(settings_mod.os, "replace", _boom)

    with pytest.raises(OSError):
        save_pruning_state(
            {"first_missed_at": {"dev1:dead-session": 1.0, "dev2:also-gone": 2.0}}
        )

    reloaded = load_pruning_state()
    assert reloaded == GRACE_CLOCK, (
        f"the grace clock did not survive an interrupted write: {reloaded!r} -- "
        "an empty dict here means every session's prune deadline just reset"
    )


def test_failed_publish_leaves_no_debris(pruning_sidecar, monkeypatch):
    """A staging file left behind accumulates in the user's config directory.

    ``save_pruning_state()`` runs on every poll cycle, so "one stray temp file
    per failure" is not a rounding error.
    """
    save_pruning_state(GRACE_CLOCK)

    def _boom(src, dst):
        raise OSError("simulated crash during publish")

    monkeypatch.setattr(settings_mod.os, "replace", _boom)
    with pytest.raises(OSError):
        save_pruning_state({"first_missed_at": {}})

    leftovers = [p for p in pruning_sidecar.parent.iterdir() if p != pruning_sidecar]
    assert leftovers == [], (
        f"temp file(s) left behind after a failed write: {leftovers}"
    )


# ---------------------------------------------------------------------------
# Properties the atomic path depends on to actually be atomic
# ---------------------------------------------------------------------------


def test_temp_file_lives_in_the_same_directory_as_the_target(
    pruning_sidecar, monkeypatch
):
    """``os.replace()`` is only atomic *within* a filesystem.

    A staging file in ``/tmp`` turns the publish step into a cross-device copy
    -- exactly the non-atomic write this file exists to prevent -- and fails
    outright with ``EXDEV`` when ``~/.config`` is on another mount (the common
    case for an encrypted or network-mounted home).
    """
    captured: list[str] = []
    monkeypatch.setattr(
        settings_mod.os, "replace", lambda src, dst: captured.append(str(src))
    )

    save_pruning_state(GRACE_CLOCK)

    assert captured
    assert Path(captured[-1]).parent == pruning_sidecar.parent


def test_temp_file_name_is_unique_per_write(pruning_sidecar, monkeypatch):
    """No writer may ever share a staging path with another.

    ``pruning.json`` has one writer process today (the server's poll cycle), so
    this is insurance rather than the fix for an observed race -- but it is the
    race muxplex-673 measured on the shared ``<target>.tmp`` path in
    ``manifest.py``/``state.py``: 3007 ``FileNotFoundError`` out of 12000 writes
    from four processes, plus interleaved bytes atomically publishing a mixture.
    Reusing the shared helper means this file cannot acquire that bug later by
    growing a second writer.
    """
    captured: list[str] = []
    monkeypatch.setattr(
        settings_mod.os, "replace", lambda src, dst: captured.append(str(src))
    )

    save_pruning_state(GRACE_CLOCK)
    save_pruning_state({"first_missed_at": {}})

    assert len(captured) == 2
    assert captured[0] != captured[1], (
        "two writers would collide on a shared staging path"
    )


def test_contents_are_fsynced_before_the_replace(pruning_sidecar, monkeypatch):
    """``os.replace()`` gives atomic *visibility*, not durability.

    Without an fsync of the staging file first, a power cut can leave the rename
    committed but the data blocks not -- i.e. an atomically-published empty
    file, which ``load_pruning_state()`` reads as ``{}``. Same reasoning as
    ``manifest.save_manifest()``.
    """
    order: list[str] = []
    real_fsync = settings_mod.os.fsync

    def _tracking_fsync(fd):
        order.append("fsync")
        return real_fsync(fd)

    monkeypatch.setattr(settings_mod.os, "fsync", _tracking_fsync)
    monkeypatch.setattr(
        settings_mod.os, "replace", lambda src, dst: order.append("replace")
    )

    save_pruning_state(GRACE_CLOCK)

    assert "replace" in order, "save_pruning_state() must publish via os.replace()"
    assert "fsync" in order, "the staging file's contents must be fsynced"
    assert order.index("fsync") < order.index("replace")


# ---------------------------------------------------------------------------
# ...and the atomic path must still actually save things
# ---------------------------------------------------------------------------


def test_the_sidecar_still_round_trips(pruning_sidecar):
    """Format is unchanged from the bare-write_text era: indent=2, trailing
    newline, UTF-8 -- anything reading the file by hand still sees what it did.
    """
    save_pruning_state(GRACE_CLOCK)

    assert load_pruning_state() == GRACE_CLOCK
    raw = pruning_sidecar.read_text(encoding="utf-8")
    assert json.loads(raw) == GRACE_CLOCK
    assert raw.endswith("\n")
    assert raw == json.dumps(GRACE_CLOCK, indent=2) + "\n"


def test_parent_directories_are_still_created(tmp_path, monkeypatch):
    """A fresh host has no ``~/.config/muxplex`` at all; the first save makes it.

    Pinned here as well as in ``test_pruning.py`` because the atomic path moved
    the ``mkdir`` into the shared helper -- a regression would show up as a
    first-run crash on a brand-new install, which no other test in this file
    would catch.
    """
    nested = tmp_path / "a" / "b" / "pruning.json"
    monkeypatch.setattr(pruning_mod, "PRUNING_STATE_PATH", nested)

    save_pruning_state(GRACE_CLOCK)

    assert nested.exists()
    assert json.loads(nested.read_text(encoding="utf-8")) == GRACE_CLOCK

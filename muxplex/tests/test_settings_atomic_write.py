"""Durability and concurrency guarantees for ``settings.json``.

Three properties are pinned here, each with the incident shape that motivates
it:

1. **The settings file is never written in place.** ``save_settings()`` writes
   a uniquely-named temp file *in the same directory*, fsyncs it, and
   ``os.replace()``s it onto the target. A crash, OOM, or power cut at any
   instant therefore leaves EITHER the complete old file OR the complete new
   one -- never a truncated one. Before this, ``save_settings()`` ended in a
   bare ``SETTINGS_PATH.write_text(...)``: an interrupted write left a partial
   JSON file, which ``load_settings()`` then silently read as "corrupt, use
   defaults" -- i.e. every view, every pin, and every server setting gone,
   with no error anywhere.

2. **A corrupt settings file is preserved, not overwritten.** ``load_settings()``
   still falls back to defaults (raising would take down every endpoint and the
   CLI over a recoverable data problem), but it first moves the unreadable bytes
   aside to a named quarantine path and logs an ERROR naming it. The old
   behaviour -- swallow ``JSONDecodeError``, return defaults -- meant the very
   next ``save_settings()`` wrote defaults over the user's real configuration,
   destroying the only copy of it.

3. **Every in-process read-modify-write of settings stays ``await``-free.**
   muxplex's server is a single-threaded asyncio application: all 54 endpoints
   are ``async def`` and nothing uses threads or an executor, so a
   ``load_settings()`` ... ``save_settings()`` sequence containing no ``await``
   between them cannot be interleaved with any other writer in this process.
   That is what currently makes the poll loop's two unlocked writers
   (``_run_poll_cycle``'s normalize and prune steps) safe -- but it is
   *incidental*, not enforced: inserting a single ``await`` into one of those
   blocks would silently reintroduce a lost-update race with no test failing.
   ``test_settings_read_modify_write_blocks_contain_no_await`` is that test.
   See ``_awaits_between_load_and_save()`` for the exact rule, and
   ``test_await_scanner_detects_a_planted_violation`` for proof it can actually
   find something when something is there to find.

Property 3's argument holds only WITHIN this process. The ``muxplex`` CLI
writes settings.json from a separate one while the server runs, and that race
is closed by an advisory cross-process lock spanning each read-modify-write --
pinned separately in ``test_settings_cross_process_lock.py``, which also
carries the reasoning for why a compare-and-swap could not have done the job.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import stat
import textwrap
from pathlib import Path

import pytest

import muxplex.settings as settings_mod
from muxplex.settings import load_settings, save_settings

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def redirect_settings_path(tmp_path, monkeypatch):
    """Redirect SETTINGS_PATH to a per-test temp file.

    Belt-and-suspenders with conftest's autouse ``_isolate_settings_path``:
    these tests deliberately provoke failed and interrupted writes, so it
    matters twice over that they cannot reach a real ``settings.json``.
    """
    fake_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_path)
    return fake_path


# ---------------------------------------------------------------------------
# 1. The target file is never written in place
# ---------------------------------------------------------------------------


def test_save_settings_publishes_via_os_replace_not_in_place(
    redirect_settings_path, monkeypatch
):
    """The headline atomicity property.

    With ``os.replace`` stubbed out to record-and-do-nothing, a full
    ``save_settings()`` must leave the target file BYTE-FOR-BYTE unchanged --
    proving no code path writes the target directly. A bare ``write_text()``
    implementation fails this immediately: the target changes even though the
    publish step never ran.
    """
    save_settings({"port": 8088})
    original_bytes = redirect_settings_path.read_bytes()

    calls: list[tuple[str, str]] = []

    def _record_only(src, dst):
        calls.append((str(src), str(dst)))

    monkeypatch.setattr(settings_mod.os, "replace", _record_only)
    save_settings({"port": 9999})

    assert calls, "save_settings() must publish via os.replace()"
    src, dst = calls[-1]
    assert dst == str(redirect_settings_path)
    assert redirect_settings_path.read_bytes() == original_bytes, (
        "the target file was modified without going through os.replace() -- "
        "an interrupted write can therefore truncate it"
    )


def test_temp_file_lives_in_the_same_directory_as_the_target(
    redirect_settings_path, monkeypatch
):
    """``os.replace()`` is only atomic *within* a filesystem.

    A temp file in ``/tmp`` (or anywhere else) turns the publish step into a
    cross-device copy, which is exactly the non-atomic write this whole file
    exists to prevent -- and it fails outright with ``EXDEV`` when the settings
    directory is on another mount, which is the common case for an encrypted or
    network-mounted home.
    """
    captured: list[str] = []
    monkeypatch.setattr(
        settings_mod.os, "replace", lambda src, dst: captured.append(str(src))
    )
    save_settings({"port": 8088})

    assert captured
    assert Path(captured[-1]).parent == redirect_settings_path.parent


def test_temp_file_name_is_unique_per_write(redirect_settings_path, monkeypatch):
    """Two writers must never share one temp path.

    ``state.py``/``manifest.py`` use a fixed ``<target>.tmp`` sibling, which is
    fine for files only this process writes. ``settings.json`` is different:
    the ``muxplex`` CLI (``settings set``, ``session-command add``, ``reset``)
    writes it from a SEPARATE process while the server is running. Two
    processes sharing one temp path interleave their bytes into it, and then
    each atomically publishes the resulting mixture -- an atomic rename of
    corrupt content is still corrupt content.
    """
    captured: list[str] = []
    monkeypatch.setattr(
        settings_mod.os, "replace", lambda src, dst: captured.append(str(src))
    )
    save_settings({"port": 8088})
    save_settings({"port": 8089})

    assert len(captured) == 2
    assert captured[0] != captured[1], (
        "concurrent writers would collide on a shared temp path"
    )


def test_contents_are_fsynced_before_the_replace(redirect_settings_path, monkeypatch):
    """``os.replace()`` gives atomic *visibility*, not durability.

    Without an fsync of the temp file first, a power cut can leave the rename
    committed but the data blocks not -- i.e. an atomically-published empty or
    partial file. Same reasoning as ``manifest.save_manifest()``.
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

    save_settings({"port": 8088})

    assert "replace" in order, "save_settings() must publish via os.replace()"
    assert order.index("fsync") < order.index("replace")


def test_failed_publish_leaves_the_original_intact_and_no_debris(
    redirect_settings_path, monkeypatch
):
    """A write that dies mid-flight must not damage what is already on disk.

    Asserts all three halves of "safe failure": the exception propagates (the
    caller is told), the previous settings survive complete and parseable, and
    no temp file is left behind to accumulate in the user's config directory.
    """
    save_settings({"port": 8088})
    before = redirect_settings_path.read_text()

    def _boom(src, dst):
        raise OSError("simulated crash during publish")

    monkeypatch.setattr(settings_mod.os, "replace", _boom)
    with pytest.raises(OSError):
        save_settings({"port": 9999})

    assert redirect_settings_path.read_text() == before
    assert json.loads(before)["port"] == 8088

    # The sidecar lock file is not debris: it is a permanent, empty,
    # deliberately NEVER-unlinked fixture of the config directory (see
    # settings.settings_lock_path -- removing it between writes would break
    # the mutual exclusion it exists to provide, because two processes would
    # end up holding locks on two different inodes). Excluded by exact path
    # rather than by a name pattern, so a real temp file can never slip
    # through this exemption.
    lock_file = settings_mod.settings_lock_path()
    leftovers = [
        p
        for p in redirect_settings_path.parent.iterdir()
        if p != redirect_settings_path and p != lock_file and p.is_file()
    ]
    assert leftovers == [], (
        f"temp file(s) left behind after a failed write: {leftovers}"
    )


def test_existing_file_permissions_survive_a_write(redirect_settings_path):
    """``settings.json`` holds the federation key and TLS material.

    ``os.replace()`` publishes the TEMP file's mode, so a naive implementation
    silently resets whatever mode the operator (or an earlier muxplex version)
    put on the real file. Tightening or loosening it behind their back is a
    security change nobody asked for.
    """
    save_settings({"port": 8088})
    os.chmod(redirect_settings_path, 0o640)

    save_settings({"port": 8089})

    assert stat.S_IMODE(redirect_settings_path.stat().st_mode) == 0o640


def test_a_freshly_created_settings_file_is_not_world_readable(redirect_settings_path):
    """First run: no prior mode to preserve, so pick the safe one.

    Consistent with how every other secret-bearing file muxplex creates is
    treated (``federation_key``, TLS private keys, the ttyd socket).
    """
    assert not redirect_settings_path.exists()
    save_settings({"port": 8088})

    mode = stat.S_IMODE(redirect_settings_path.stat().st_mode)
    assert mode & 0o077 == 0, f"fresh settings.json is group/world accessible: {mode:o}"


def test_settings_survive_a_normal_round_trip(redirect_settings_path):
    """The atomic path must still actually save things."""
    save_settings({"port": 1234, "views": [{"name": "Focus", "sessions": ["alpha"]}]})

    reloaded = load_settings()
    assert reloaded["port"] == 1234
    assert reloaded["views"] == [{"name": "Focus", "sessions": ["alpha"]}]
    assert redirect_settings_path.read_text().endswith("\n")


# ---------------------------------------------------------------------------
# 2. A corrupt settings file is preserved, not overwritten
# ---------------------------------------------------------------------------


def test_corrupt_settings_are_quarantined_and_reported(
    redirect_settings_path, caplog, tmp_path
):
    """The user's data is moved aside under a named path, never just dropped.

    A torn file written before the atomic-write fix landed (or damaged by
    anything else -- a full disk, a bad hand-edit) used to be read as
    "unparseable, use defaults", and the next write then overwrote it with
    defaults. The bytes were the only copy of ~10 views of pins.
    """
    corrupt = '{"views": [{"name": "Focus", "sessi'
    redirect_settings_path.write_text(corrupt)

    with caplog.at_level(logging.ERROR, logger="muxplex.settings"):
        result = load_settings()

    assert result["port"] == settings_mod.DEFAULT_SETTINGS["port"]

    quarantined = sorted(tmp_path.glob("settings.json.corrupt-*"))
    assert len(quarantined) == 1, "the unreadable bytes were not preserved"
    assert quarantined[0].read_text() == corrupt

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert quarantined[0].name in logged, (
        "the ERROR log must name the quarantine path, or the user cannot recover"
    )


def test_corrupt_settings_are_quarantined_exactly_once(
    redirect_settings_path, tmp_path
):
    """``load_settings()`` runs on essentially every request.

    Quarantining by COPY would mint a new file per call and fill the config
    directory within seconds; quarantining by MOVE is self-limiting -- the
    unreadable file is gone from the load path, so the next call is an ordinary
    "no settings file yet".
    """
    redirect_settings_path.write_text("{not json")

    for _ in range(5):
        load_settings()

    assert len(sorted(tmp_path.glob("settings.json.corrupt-*"))) == 1
    assert not redirect_settings_path.exists()


def test_a_missing_settings_file_is_not_treated_as_corruption(
    redirect_settings_path, tmp_path
):
    """First run is normal, not an incident: no quarantine file, no ERROR."""
    assert not redirect_settings_path.exists()
    load_settings()
    assert sorted(tmp_path.glob("settings.json.corrupt-*")) == []


# ---------------------------------------------------------------------------
# 3. In-process read-modify-write blocks stay await-free
# ---------------------------------------------------------------------------


def _contains_call(node: ast.AST, name: str) -> bool:
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name
        for n in ast.walk(node)
    )


def _awaits_between_load_and_save(source: str, filename: str) -> list[str]:
    """Return ``"<file>:<line>"`` for every ``save_settings()`` call whose
    read-modify-write window contains an ``await``.

    The window is found without a full control-flow graph, by the same
    ascend-to-the-nearest-enclosing-scope rule a reader applies by eye: for
    each ``save_settings()`` call, take the statement list it sits in and scan
    BACKWARDS for the nearest statement that performs a ``load_settings()``.
    If this level has none, ascend one statement list and repeat. Once found,
    every statement in the span between them is checked for ``ast.Await``.

    Deliberately conservative in both directions. It never invents a window
    (no ``load_settings()`` above a ``save_settings()`` anywhere in the
    enclosing scopes means nothing to check -- e.g. a caller handed a dict it
    loaded elsewhere), and it stops at the FIRST level that supplies a load
    rather than reporting the same call once per enclosing block.
    """
    tree = ast.parse(source, filename=filename)

    all_statements: list[ast.stmt] = []
    location: dict[int, tuple[list[ast.stmt], int]] = {}
    owner_of_body: dict[int, ast.stmt] = {}

    def index_body(body: list[ast.stmt], owner: ast.stmt | None) -> None:
        if owner is not None:
            owner_of_body[id(body)] = owner
        for position, stmt in enumerate(body):
            all_statements.append(stmt)
            location[id(stmt)] = (body, position)
            for nested in _child_bodies(stmt):
                index_body(nested, stmt)

    index_body(tree.body, None)

    offenders: list[str] = []
    for statement in all_statements:
        if not _directly_contains(statement, "save_settings"):
            continue
        current: ast.stmt | None = statement
        while current is not None:
            body, position = location[id(current)]
            load_at = next(
                (
                    back
                    for back in range(position, -1, -1)
                    if _contains_call(body[back], "load_settings")
                ),
                None,
            )
            if load_at is not None:
                window = body[load_at : position + 1]
                if any(
                    isinstance(inner, ast.Await)
                    for stmt in window
                    for inner in ast.walk(stmt)
                ):
                    offenders.append(f"{filename}:{statement.lineno}")
                break
            current = owner_of_body.get(id(body))
    return offenders


def _child_bodies(stmt: ast.stmt) -> list[list[ast.stmt]]:
    """Every statement list *stmt* owns (if/try/with/for/while/def bodies)."""
    bodies: list[list[ast.stmt]] = []
    for field in ("body", "orelse", "finalbody"):
        nested = getattr(stmt, field, None)
        if isinstance(nested, list) and nested and isinstance(nested[0], ast.stmt):
            bodies.append(nested)
    for handler in getattr(stmt, "handlers", []):
        bodies.append(handler.body)
    return bodies


def _directly_contains(stmt: ast.stmt, name: str) -> bool:
    """True when *stmt* is the INNERMOST statement holding a call to *name*.

    Anchors each window to the smallest statement that actually performs the
    call, so ``if x: save_settings(s)`` reports the call, not the ``if``.
    """
    return _contains_call(stmt, name) and not any(
        _contains_call(child, name) for body in _child_bodies(stmt) for child in body
    )


@pytest.mark.parametrize("module", ["main.py", "settings.py"])
def test_settings_read_modify_write_blocks_contain_no_await(module: str):
    """No settings read-modify-write may yield to the event loop mid-flight.

    This is the whole basis on which the poll loop's two unlocked writers
    (``_run_poll_cycle``'s session-key normalize and stale-key prune steps) are
    safe without a lock, and it is a structural property, not a convention:
    every muxplex endpoint is ``async def`` and the package uses no threads and
    no executor, so a synchronous load-mutate-save sequence runs to completion
    with nothing else able to interleave. Add one ``await`` inside such a block
    and two writers can read the same settings, each mutate their own copy, and
    the second save silently discards the first -- which looks to the user
    exactly like "the pin didn't stick".

    Fail here and the fix is one of: move the ``await`` out of the window, or
    give that path a real lock. Do not delete the assertion.
    """
    path = REPO_ROOT / "muxplex" / module
    offenders = _awaits_between_load_and_save(path.read_text(), module)
    assert offenders == [], (
        "settings read-modify-write window contains an await (lost-update race): "
        + ", ".join(offenders)
    )


def test_await_scanner_detects_a_planted_violation():
    """Prove the scanner above can fail, so a green result means something."""
    planted = textwrap.dedent(
        """
        async def poll_cycle():
            settings = load_settings()
            await do_something_slow()
            settings["views"] = []
            save_settings(settings)
        """
    )
    assert _awaits_between_load_and_save(planted, "planted.py")


def test_await_scanner_accepts_a_clean_block():
    """...and that it does not fire on the shape the code actually uses."""
    clean = textwrap.dedent(
        """
        async def poll_cycle():
            await something_before()
            try:
                settings = load_settings()
                settings["views"] = []
                save_settings(settings)
            except Exception:
                pass
            await something_after()
        """
    )
    assert _awaits_between_load_and_save(clean, "clean.py") == []

"""Cross-process concurrency guarantees for ``settings.json``.

``test_settings_atomic_write.py`` pins the two properties that make a settings
write safe WITHIN this process: the file is published atomically, and no
in-process read-modify-write yields to the event loop mid-flight. That second
argument -- "the server is single-threaded asyncio, so a load..save sequence
with no ``await`` in it cannot be interleaved" -- is airtight, and it stops
dead at the process boundary.

The ``muxplex`` CLI writes settings.json from a SEPARATE process while the
server is running: ``config set`` / ``config reset`` (via ``patch_settings``),
``commands add`` / ``commands rm``, ``tls setup``. Each is its own load ->
mutate -> save. If the poll cycle's normalize or prune step saves between the
CLI's load and its save (or vice versa), one side's change is silently
discarded -- no error, no log, no retry. After the atomic-write work neither
side can produce a TORN file any more, so what is left is purely a lost
update, with the same user-visible signature as everything else in this
family: "I changed it and it didn't stick."

``settings.settings_write_lock()`` closes that with an advisory ``flock`` on a
sidecar lockfile, held across the WHOLE read-modify-write. Three things are
pinned here:

1. **The race is real and this harness provokes it.**
   ``test_two_unlocked_writers_lose_updates`` runs two genuinely concurrent
   OS processes that read, mutate, and save without holding the lock across
   the window, and asserts updates ARE lost. Without this, a green result
   from (2) would prove nothing -- it could just mean the writers never
   actually overlapped. Note that those writers still go through
   ``save_settings()``, which takes the lock for the write itself: this test
   is therefore also the standing proof of *why the lock has to span the
   window*, because locking only the write demonstrably prevents nothing.

2. **With the lock, nothing is lost.** ``test_two_locked_writers_lose_nothing``
   is the same two processes, same timing, same contention, with the window
   held -- every single update survives.

3. **Every real write path actually takes it.** A behavioural test can only
   cover the paths it calls. ``test_every_settings_read_modify_write_holds_the_lock``
   is an AST scan of ``main.py``/``cli.py``/``settings.py`` that fails if any
   load..save window is left unprotected, so a future edit cannot quietly
   reintroduce the bug somewhere this file never exercises.

Every child process in this module gets its settings path from
``MUXPLEX_TEST_SETTINGS_PATH`` and REFUSES TO RUN without it (see
``_WORKER_SOURCE``). A subprocess does not inherit conftest's autouse
``SETTINGS_PATH`` monkeypatch -- that is an in-process attribute rebind -- so
the isolation that protects the rest of the suite from the host's real
``~/.config/muxplex/settings.json`` has to be re-established explicitly on the
far side of ``fork``. Failing closed rather than defaulting is what makes a
mistake here impossible rather than merely unlikely.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

import muxplex.settings as settings_mod

# The AST primitives are imported, not re-implemented: "what counts as a child
# statement body" must mean the same thing to both scanners, or one of them
# will silently stop seeing a shape the other still checks.
from muxplex.tests.test_settings_atomic_write import (
    _child_bodies,
    _contains_call,
    _directly_contains,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# Timing for the two-writer tests. The hold is what forces the writers'
# read-modify-write windows to overlap: without it two processes can complete
# a whole cycle in well under a millisecond and interleave only by luck, which
# is how a concurrency test ends up passing for the wrong reason. 40ms is far
# longer than a real critical section while still keeping this ordinary
# contention test quick.
WRITER_ITERATIONS = 15
WRITER_HOLD_S = 0.04
WRITER_TIMEOUT_S = 120.0
LONG_LOCK_HOLD_S = 2.25

_WORKER_SOURCE = '''\
"""Child-process settings writer. Not a test -- see the module that writes it.

Refuses to run without MUXPLEX_TEST_SETTINGS_PATH: this process does NOT
inherit the suite's in-process SETTINGS_PATH monkeypatch, so an absent value
would mean writing to the REAL ~/.config/muxplex/settings.json. Exit non-zero
instead.
"""

import os
import sys
import time
from pathlib import Path

_target = os.environ.get("MUXPLEX_TEST_SETTINGS_PATH")
if not _target:
    sys.stderr.write("MUXPLEX_TEST_SETTINGS_PATH unset -- refusing to write\\n")
    sys.exit(3)

import muxplex.settings as settings_mod

settings_mod.SETTINGS_PATH = Path(_target)

mode, marker, iterations, hold_s, gate = (
    sys.argv[1],
    sys.argv[2],
    int(sys.argv[3]),
    float(sys.argv[4]),
    Path(sys.argv[5]),
)

# Start gate: both children are already spawned and imported by the time this
# releases, so the contention is real rather than an artifact of one process
# happening to finish importing first.
while not gate.exists():
    time.sleep(0.005)


def _one_cycle(index):
    settings = settings_mod.load_settings()
    settings["hidden_sessions"] = list(settings.get("hidden_sessions") or []) + [
        "%s-%03d" % (marker, index)
    ]
    # Hold the window open. Any real writer's window is open too -- this just
    # makes the overlap deterministic instead of a coin flip.
    time.sleep(hold_s)
    settings_mod.save_settings(settings)


for _i in range(iterations):
    if mode == "locked":
        with settings_mod.settings_write_lock():
            _one_cycle(_i)
    elif mode == "unlocked":
        # save_settings() still takes the lock internally for the write. This
        # is the point: the WINDOW is what has to be exclusive.
        _one_cycle(_i)
    else:
        sys.exit("unknown mode %r" % mode)
'''


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    """A per-test settings.json, in this process and in every child."""
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", path)
    return path


@pytest.fixture
def worker_script(tmp_path):
    script = tmp_path / "settings_writer.py"
    script.write_text(_WORKER_SOURCE, encoding="utf-8")
    return script


def _child_env(settings_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["MUXPLEX_TEST_SETTINGS_PATH"] = str(settings_path)
    # The child imports muxplex from the repo, not from wherever it was
    # invoked; keep any existing PYTHONPATH so a venv-less run still works.
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    return env


def _run_two_writers(
    script: Path,
    settings_path: Path,
    mode: str,
    gate: Path,
) -> list[str]:
    """Run two REAL concurrent writer processes; return the surviving markers.

    Both are spawned before the gate file is created, so neither gets a head
    start from process startup or module import time.
    """
    env = _child_env(settings_path)
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                str(script),
                mode,
                marker,
                str(WRITER_ITERATIONS),
                str(WRITER_HOLD_S),
                str(gate),
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for marker in ("alpha", "beta")
    ]
    try:
        # Let both reach the gate loop before releasing them.
        time.sleep(0.5)
        gate.write_text("go", encoding="utf-8")
        for proc in procs:
            _out, err = proc.communicate(timeout=WRITER_TIMEOUT_S)
            assert proc.returncode == 0, f"writer failed ({proc.returncode}): {err}"
    finally:
        for proc in procs:
            if proc.poll() is None:  # pragma: no cover - only on a hung child
                proc.kill()

    return list(json.loads(settings_path.read_text())["hidden_sessions"])


# ---------------------------------------------------------------------------
# 1. The race is real, and locking only the write does not close it
# ---------------------------------------------------------------------------


def test_two_unlocked_writers_lose_updates(settings_file, worker_script, tmp_path):
    """Two real processes, windows unprotected -> updates vanish silently.

    This is the bug, reproduced: no exception, no log, no failed exit status
    on either side -- just fewer entries on disk than the two processes
    between them wrote.

    It is also the proof that ``settings_write_lock()`` has to span the whole
    read-modify-write. These writers DO reach the lock, because
    ``save_settings()`` takes it for the write itself; that is not enough and
    this test is what says so out loud. If someone later "simplifies" the fix
    down to locking just the write, test 2 below starts failing and this one
    keeps passing -- which is the correct pair of signals.
    """
    settings_file.write_text(json.dumps({"hidden_sessions": []}), encoding="utf-8")

    survived = _run_two_writers(
        worker_script, settings_file, "unlocked", tmp_path / "gate-unlocked"
    )

    expected = 2 * WRITER_ITERATIONS
    assert len(survived) < expected, (
        "expected the unprotected read-modify-write windows to lose updates, but "
        f"all {expected} survived -- the two writers cannot have overlapped, so "
        "test_two_locked_writers_lose_nothing is not actually proving anything. "
        f"Raise WRITER_HOLD_S (currently {WRITER_HOLD_S}s)."
    )


# ---------------------------------------------------------------------------
# 2. With the window held, nothing is lost
# ---------------------------------------------------------------------------


def test_two_locked_writers_lose_nothing(settings_file, worker_script, tmp_path):
    """The headline guarantee: same two processes, same contention, no loss.

    Every entry both writers appended is present, exactly once. This is the
    acceptance criterion in its general form -- two concurrent CLI writers, or
    one CLI writer and one server writer, are the same shape once both hold
    the lock.
    """
    settings_file.write_text(json.dumps({"hidden_sessions": []}), encoding="utf-8")

    survived = _run_two_writers(
        worker_script, settings_file, "locked", tmp_path / "gate-locked"
    )

    expected = sorted(
        f"{marker}-{i:03d}"
        for marker in ("alpha", "beta")
        for i in range(WRITER_ITERATIONS)
    )
    assert sorted(survived) == expected, (
        "a concurrent write was lost despite the cross-process lock: "
        f"{len(survived)} of {len(expected)} survived"
    )


_LONG_HOLDER_SOURCE = '''\
"""Hold the real muxplex settings lock long enough to expose timeout bypasses."""

import json
import os
import sys
import time
from pathlib import Path

target = os.environ.get("MUXPLEX_TEST_SETTINGS_PATH")
if not target:
    sys.stderr.write("MUXPLEX_TEST_SETTINGS_PATH unset -- refusing to write\\n")
    sys.exit(3)

import muxplex.settings as settings_mod

settings_mod.SETTINGS_PATH = Path(target)
ready = Path(sys.argv[1])
hold_s = float(sys.argv[2])

with settings_mod.settings_write_lock():
    settings = settings_mod.load_settings()
    settings["hidden_sessions"] = list(settings.get("hidden_sessions") or []) + ["alpha"]
    ready.write_text("locked", encoding="utf-8")
    time.sleep(hold_s)
    settings_mod.save_settings(settings)
'''


def test_waiting_writer_never_bypasses_a_long_held_lock(settings_file, tmp_path):
    """A cooperative writer waits; it never trades its update for availability.

    The original implementation made a waiting process proceed without the
    flock after two seconds. This child holds the real lock for longer than
    that, after reading and mutating the file but before saving it. The parent
    must wait, then load the child's committed value before writing its own.

    Against the timeout-bypass implementation, ``held`` is False after roughly
    two seconds and this assertion fails; if that assertion were removed, the
    child then overwrites the parent's ``beta`` update with its stale
    ``alpha`` snapshot. The test is therefore a controlled reproduction of
    the macOS CI interleaving, independent of filesystem speed.
    """
    settings_file.write_text(json.dumps({"hidden_sessions": []}), encoding="utf-8")
    script = tmp_path / "long_lock_holder.py"
    script.write_text(_LONG_HOLDER_SOURCE, encoding="utf-8")
    ready = tmp_path / "long-lock-holder-ready"
    holder = subprocess.Popen(
        [sys.executable, str(script), str(ready), str(LONG_LOCK_HOLD_S)],
        env=_child_env(settings_file),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + WRITER_TIMEOUT_S
        while not ready.exists():
            assert time.monotonic() < deadline, "lock holder never acquired the lock"
            time.sleep(0.005)

        with settings_mod.settings_write_lock() as held:
            assert held is True
            settings = settings_mod.load_settings()
            settings["hidden_sessions"] = list(
                settings.get("hidden_sessions") or []
            ) + ["beta"]
            settings_mod.save_settings(settings)
    finally:
        out, err = holder.communicate(timeout=WRITER_TIMEOUT_S)
        assert holder.returncode == 0, (
            f"lock holder failed ({holder.returncode}): {out}{err}"
        )

    assert json.loads(settings_file.read_text())["hidden_sessions"] == ["alpha", "beta"]


_CRASH_HOLDER_SOURCE = '''\
"""Acquire the real lock, then exit without releasing it in user-space."""

import os
import sys
from pathlib import Path

target = os.environ.get("MUXPLEX_TEST_SETTINGS_PATH")
if not target:
    sys.stderr.write("MUXPLEX_TEST_SETTINGS_PATH unset -- refusing to write\\n")
    sys.exit(3)

import muxplex.settings as settings_mod

settings_mod.SETTINGS_PATH = Path(target)
ready = Path(sys.argv[1])
with settings_mod.settings_write_lock():
    ready.write_text("locked", encoding="utf-8")
    os._exit(0)
'''


def test_kernel_releases_the_lock_when_a_holder_crashes(settings_file, tmp_path):
    """A dead holder cannot strand a future writer behind the blocking flock."""
    script = tmp_path / "crash_lock_holder.py"
    script.write_text(_CRASH_HOLDER_SOURCE, encoding="utf-8")
    ready = tmp_path / "crash-lock-holder-ready"
    holder = subprocess.Popen(
        [sys.executable, str(script), str(ready)],
        env=_child_env(settings_file),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + WRITER_TIMEOUT_S
    while not ready.exists():
        assert time.monotonic() < deadline, "crash holder never acquired the lock"
        time.sleep(0.005)
    out, err = holder.communicate(timeout=WRITER_TIMEOUT_S)
    assert holder.returncode == 0, (
        f"crash holder failed ({holder.returncode}): {out}{err}"
    )

    with settings_mod.settings_write_lock() as held:
        assert held is True
        settings_mod.save_settings({"sort_order": "name"})

    assert json.loads(settings_file.read_text())["sort_order"] == "name"


# ---------------------------------------------------------------------------
# 3. The named acceptance case: a real `muxplex config set` against a
#    poll-cycle-shaped writer
# ---------------------------------------------------------------------------


_CLI_RACE_SOURCE = '''\
"""Poll-cycle-shaped settings writer, for racing against the real CLI."""

import os
import sys
import time
from pathlib import Path

_target = os.environ.get("MUXPLEX_TEST_SETTINGS_PATH")
if not _target:
    sys.stderr.write("MUXPLEX_TEST_SETTINGS_PATH unset -- refusing to write\\n")
    sys.exit(3)

import muxplex.settings as settings_mod

settings_mod.SETTINGS_PATH = Path(_target)
gate = Path(sys.argv[1])
hold_s = float(sys.argv[2])

while not gate.exists():
    time.sleep(0.005)

# The exact shape of _run_poll_cycle()'s normalize/prune steps: lock, load,
# mutate, save. `session_filter` is a syncable display key the CLI side does
# not touch, so the two writers are provably editing different keys of the
# same file.
with settings_mod.settings_write_lock():
    settings = settings_mod.load_settings()
    settings["session_filter"] = "poll-cycle-wrote-this"
    time.sleep(hold_s)
    settings_mod.save_settings(settings)
'''


def test_cli_config_set_racing_a_server_write_loses_neither(tmp_path, settings_file):
    """`muxplex config set` in one process, a poll-cycle write in another.

    This is acceptance criterion 1 as literally as it can be run without a
    live server: the REAL CLI entry point (``python -m muxplex config set``,
    which reaches ``patch_settings()``) in its own OS process, overlapping a
    writer with the poll cycle's exact lock/load/mutate/save shape. Both
    changes must be on disk afterwards.

    ``HOME`` is redirected for the CLI child because ``SETTINGS_PATH`` is
    derived from ``Path.home()`` at import time and the CLI has no flag to
    override it -- so pointing HOME at ``tmp_path`` is what keeps a real
    ``muxplex`` invocation off the host's real config. The settings file
    fixture is re-pointed to match.
    """
    config_dir = tmp_path / ".config" / "muxplex"
    config_dir.mkdir(parents=True)
    real_settings = config_dir / "settings.json"
    real_settings.write_text(
        json.dumps({"sort_order": "manual", "session_filter": ""}), encoding="utf-8"
    )

    script = tmp_path / "poll_cycle_writer.py"
    script.write_text(_CLI_RACE_SOURCE, encoding="utf-8")
    gate = tmp_path / "gate-cli"

    server_env = _child_env(real_settings)
    cli_env = _child_env(real_settings)
    cli_env["HOME"] = str(tmp_path)

    server = subprocess.Popen(
        [sys.executable, str(script), str(gate), str(WRITER_HOLD_S)],
        env=server_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # Give the poll-cycle writer time to import and reach the gate, then
        # release it and immediately start the CLI on top of it.
        time.sleep(0.5)
        gate.write_text("go", encoding="utf-8")
        cli = subprocess.run(
            [sys.executable, "-m", "muxplex", "config", "set", "sort_order", "name"],
            env=cli_env,
            capture_output=True,
            text=True,
            timeout=WRITER_TIMEOUT_S,
        )
        _out, err = server.communicate(timeout=WRITER_TIMEOUT_S)
    finally:
        if server.poll() is None:  # pragma: no cover - only on a hung child
            server.kill()

    assert server.returncode == 0, f"poll-cycle writer failed: {err}"
    assert cli.returncode == 0, f"muxplex config set failed: {cli.stderr}"

    final = json.loads(real_settings.read_text())
    assert final["sort_order"] == "name", (
        "the CLI's write was silently discarded by the concurrent server write"
    )
    assert final["session_filter"] == "poll-cycle-wrote-this", (
        "the server's write was silently discarded by the concurrent CLI write"
    )


# ---------------------------------------------------------------------------
# 4. Every read-modify-write in the package actually holds the lock
# ---------------------------------------------------------------------------
#
# Same window-finding rule as test_settings_atomic_write's await scanner: for
# each save_settings() call, ascend to the nearest enclosing statement list
# that supplies a load_settings(). Where that scanner asks "does this window
# contain an await", this one asks "is this window inside a
# settings_write_lock()".


def _rmw_windows_without_the_lock(source: str, filename: str) -> list[str]:
    """``"<file>:<line>"`` for every read-modify-write not under the lock.

    A window counts as protected when the lock is taken anywhere at or above
    it: an enclosing ``with settings_write_lock()``, or the enclosing function
    carrying the ``@_under_settings_write_lock`` decorator (how
    ``patch_settings``/``apply_synced_settings`` take it, since their window
    IS the whole call).

    Conservative in the same direction as the await scanner: no
    ``load_settings()`` above a ``save_settings()`` in the enclosing scopes
    means there is no window here to protect (the caller was handed a dict it
    loaded elsewhere), so nothing is reported.

    The ascent STOPS at the enclosing function. A read-modify-write window
    cannot span a ``def``, and continuing into module scope matches any
    earlier top-level statement that happens to contain a ``load_settings()``
    call -- which reported ``config_reset(None)``'s blind
    ``save_settings(DEFAULT_SETTINGS)`` (a write with no read at all, and
    deliberately a clobber) as an unprotected window.
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

    def takes_the_lock(stmt: ast.stmt) -> bool:
        if isinstance(stmt, ast.With | ast.AsyncWith):
            if any(
                _contains_call(item.context_expr, "settings_write_lock")
                for item in stmt.items
            ):
                return True
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            if any(
                isinstance(dec, ast.Name) and dec.id == "_under_settings_write_lock"
                for dec in stmt.decorator_list
            ):
                return True
        return False

    offenders: list[str] = []
    for statement in all_statements:
        if not _directly_contains(statement, "save_settings"):
            continue
        current: ast.stmt | None = statement
        window_found = False
        protected = False
        while current is not None:
            body, position = location[id(current)]
            if not window_found:
                load_at = next(
                    (
                        back
                        for back in range(position, -1, -1)
                        if _contains_call(body[back], "load_settings")
                    ),
                    None,
                )
                window_found = load_at is not None
            owner = owner_of_body.get(id(body))
            if owner is not None and takes_the_lock(owner):
                protected = True
                break
            if isinstance(owner, ast.FunctionDef | ast.AsyncFunctionDef):
                break
            current = owner
        if window_found and not protected:
            offenders.append(f"{filename}:{statement.lineno}")
    return offenders


@pytest.mark.parametrize("module", ["main.py", "cli.py", "settings.py"])
def test_every_settings_read_modify_write_holds_the_lock(module: str):
    """No load..save window may run without the cross-process lock.

    The behavioural tests above can only cover the paths they call; this
    covers every path that exists. Fail here and the fix is to wrap the window
    named in the failure with ``settings_write_lock()`` -- NOT to narrow this
    assertion. A window left open is a silent lost update between the server
    and the CLI, which is exactly the class of bug this whole file exists to
    close.
    """
    path = REPO_ROOT / "muxplex" / module
    offenders = _rmw_windows_without_the_lock(path.read_text(), module)
    assert offenders == [], (
        "settings read-modify-write window runs without settings_write_lock() "
        "(cross-process lost update): " + ", ".join(offenders)
    )


def test_lock_scanner_detects_a_planted_violation():
    """Prove the scanner above can fail, so a green result means something."""
    planted = textwrap.dedent(
        """
        def poll_cycle():
            settings = load_settings()
            settings["views"] = []
            save_settings(settings)
        """
    )
    assert _rmw_windows_without_the_lock(planted, "planted.py")


def test_lock_scanner_accepts_both_protected_shapes():
    """...and that it accepts the two shapes the code actually uses."""
    with_block = textwrap.dedent(
        """
        def poll_cycle():
            with settings_write_lock():
                settings = load_settings()
                settings["views"] = []
                save_settings(settings)
        """
    )
    decorated = textwrap.dedent(
        """
        @_under_settings_write_lock
        def patch_settings(patch):
            current = load_settings()
            current.update(patch)
            save_settings(current)
        """
    )
    assert _rmw_windows_without_the_lock(with_block, "with.py") == []
    assert _rmw_windows_without_the_lock(decorated, "decorated.py") == []


def test_lock_scanner_does_not_invent_a_window_across_a_function_boundary():
    """A blind write in its own function is not an unprotected window.

    ``config_reset(None)`` writes ``DEFAULT_SETTINGS`` with no read at all --
    it is a deliberate clobber, and there is nothing for a lock to make
    consistent. Without the function-boundary stop, the scanner walked out to
    module scope, matched an unrelated earlier function's
    ``load_settings()``, and reported it.
    """
    planted = textwrap.dedent(
        """
        def somewhere_else():
            settings = load_settings()
            return settings

        def reset_everything():
            save_settings(DEFAULTS)
        """
    )
    assert _rmw_windows_without_the_lock(planted, "boundary.py") == []


# ---------------------------------------------------------------------------
# 5. The lock's own contracts
# ---------------------------------------------------------------------------


def test_lock_is_reentrant_within_one_process(settings_file):
    """A nested acquire must not deadlock the process against itself.

    ``load_settings()`` can call ``save_settings()`` (the showHoverPreview
    migration) and ``save_settings()`` takes the lock, so nesting is a normal
    occurrence on the real code path, not a hypothetical. Taking a second fd
    on the same file instead of reusing the held one would block forever.
    """
    with settings_mod.settings_write_lock() as outer_held:
        with settings_mod.settings_write_lock() as inner_held:
            assert outer_held is True
            assert inner_held is True
            settings = settings_mod.load_settings()
            settings["sort_order"] = "name"
            settings_mod.save_settings(settings)

    assert settings_mod._settings_lock_depth == 0
    assert settings_mod._settings_lock_fd is None
    assert json.loads(settings_file.read_text())["sort_order"] == "name"


def test_lock_is_released_when_the_body_raises(settings_file):
    """An exception inside the window must not strand the lock.

    A stranded lock would make every subsequent writer -- server and CLI --
    block forever. Releasing it in ``finally`` is therefore necessary for both
    correctness and availability.
    """
    with pytest.raises(RuntimeError):
        with settings_mod.settings_write_lock():
            raise RuntimeError("boom")

    assert settings_mod._settings_lock_depth == 0
    assert settings_mod._settings_lock_fd is None
    with settings_mod.settings_write_lock() as held:
        assert held is True


def test_lock_path_follows_settings_path_and_is_a_sidecar(settings_file, tmp_path):
    """The lockfile is beside settings.json, never settings.json itself.

    ``save_settings()`` publishes by ``os.replace()``, which swaps in a new
    inode. A lock held on settings.json's own inode would stop excluding
    anyone the moment the first write landed -- the lock would still be
    "held", and would protect nothing.
    """
    assert settings_mod.settings_lock_path() == tmp_path / "settings.json.lock"
    assert settings_mod.settings_lock_path() != settings_file


def test_lock_file_survives_a_write_cycle(settings_file):
    """The lockfile is created once and never unlinked.

    Unlinking it is the same bug as locking settings.json directly, in slower
    motion: holder A holds the lock on the removed inode while holder B
    creates a fresh one, and both believe they have exclusive access.
    """
    with settings_mod.settings_write_lock():
        settings_mod.save_settings({"sort_order": "name"})
    lock_path = settings_mod.settings_lock_path()
    assert lock_path.exists()
    inode = lock_path.stat().st_ino

    with settings_mod.settings_write_lock():
        settings_mod.save_settings({"sort_order": "manual"})
    assert lock_path.exists()
    assert lock_path.stat().st_ino == inode


def test_lock_acquire_failure_refuses_to_run_an_unprotected_write(
    settings_file, monkeypatch
):
    """A lock setup failure is loud; continuing would silently lose updates."""
    settings_file.write_text(json.dumps({"sort_order": "manual"}), encoding="utf-8")

    def _fail() -> int:
        raise settings_mod.SettingsWriteLockError("simulated lock failure")

    monkeypatch.setattr(settings_mod, "_acquire_settings_flock", _fail)

    with pytest.raises(settings_mod.SettingsWriteLockError, match="simulated"):
        settings_mod.save_settings({"sort_order": "name"})

    assert json.loads(settings_file.read_text())["sort_order"] == "manual"
    assert settings_mod._settings_lock_depth == 0


def test_worker_refuses_to_run_without_an_explicit_settings_path(worker_script):
    """The child's own fail-closed guard, exercised.

    A subprocess does not inherit conftest's ``SETTINGS_PATH`` monkeypatch. If
    the worker defaulted instead of refusing, a dropped environment variable
    would point 30 concurrent writes at the developer's real
    ``~/.config/muxplex/settings.json`` -- the exact incident conftest's
    module docstring records. This proves it exits instead.
    """
    env = dict(os.environ)
    env.pop("MUXPLEX_TEST_SETTINGS_PATH", None)
    env["PYTHONPATH"] = str(REPO_ROOT)

    result = subprocess.run(
        [sys.executable, str(worker_script), "locked", "x", "1", "0", "/nonexistent"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 3
    assert "refusing to write" in result.stderr

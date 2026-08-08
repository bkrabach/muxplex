"""Differential harness for the tmux-library extraction (stage S0).

See docs/plans/2026-08-08-tmux-lib-extraction-plan.md §8.2: every extraction
stage is proven safe by replaying REAL recorded inputs through the code and
asserting the results are byte-identical to the recorded baseline. This file
is the replay half; ``scripts/record_differential_fixtures.py`` is the
recording half (it drives a real tmux server on an isolated scratch
``TMUX_TMPDIR`` and writes ``fixtures/differential/recorded.json``).

At S0 (nothing moved yet) the harness is trivially green — the current code
IS the baseline. After every later move (S1's pure moves, S2's settings
inversion, S3's ``git mv`` to ``lib/``), re-running

    pytest -m differential

proves the moved code is behaviour-identical to the pre-move code:

- The sequential run_tmux replay player asserts the code issues the EXACT
  argv it issued at record time (a moved module that changes so much as a
  flag order goes red here).
- Every parsed/computed result is compared to the recorded baseline via
  canonical JSON (sort_keys) — byte-identity, not shape-identity.

The ONE deliberate exception is the case named ``PRE-S4 BASELINE`` in the
fixture: §13.3 schedules a contract change (unknown top-level manifest keys
must round-trip verbatim; today the closed-key-set rebuild drops them). That
single case pins today's behavior so S4's change is visible and deliberate —
when S4 lands, that case's expectation is re-recorded and every other case
must stay byte-identical.

These tests never touch a real tmux server: the subprocess boundary is
replayed from the tape. The ttyd AF_UNIX liveness/validation tests are the
exception — they exercise REAL sockets and directories created in a scratch
dir at test time, because a socket's liveness cannot be replayed from JSON
(recorded fixtures would assert nothing about ``connect()``).
"""

from __future__ import annotations

import copy
import json
import os
import socket
import stat as stat_module
from pathlib import Path

import pytest

import muxplex.bells as bells_mod
import muxplex.manifest as manifest_mod
import muxplex.sessions as sessions_mod
import muxplex.terminal_input as ti_mod
import muxplex.ttyd as ttyd_mod

pytestmark = pytest.mark.differential

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "differential" / "recorded.json"


def canon(obj) -> str:
    """Canonical JSON form used for byte-identity comparison."""
    return json.dumps(obj, sort_keys=True)


@pytest.fixture(scope="module")
def recorded() -> dict:
    if not FIXTURE_PATH.exists():
        pytest.fail(
            f"differential fixture missing: {FIXTURE_PATH}\n"
            "Record it with: uv run python "
            "scripts/record_differential_fixtures.py\n"
            "(A missing fixture is a broken safety net, not a skip — see "
            "docs/plans/2026-08-08-tmux-lib-extraction-plan.md §8.2.)"
        )
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def make_player(tape: list[dict]):
    """Sequential run_tmux replayer.

    Asserts the code under test issues the EXACT argv recorded from the real
    run — the argv assertion is itself differential coverage of run_tmux's
    call shape across a move.
    """
    entries = iter(tape)

    async def player(*args: str) -> str:
        try:
            entry = next(entries)
        except StopIteration:
            pytest.fail(f"replay divergence: unexpected extra tmux call {list(args)}")
        assert list(args) == entry["args"], (
            f"replay divergence: code issued {list(args)}, "
            f"recorded run issued {entry['args']}"
        )
        if "error" in entry:
            raise RuntimeError(entry["error"])
        return entry["stdout"]

    return player


@pytest.fixture(autouse=True)
def _reset_session_caches(monkeypatch):
    """Each replay starts from empty parser caches, like a fresh process."""
    monkeypatch.setattr(sessions_mod, "_session_list", [])
    monkeypatch.setattr(sessions_mod, "_snapshots", {})
    monkeypatch.setattr(sessions_mod, "_activity", {})
    monkeypatch.setattr(sessions_mod, "_created", {})
    monkeypatch.setattr(sessions_mod, "_cwds", {})


# ---------------------------------------------------------------------------
# observe: enumerate_sessions
# ---------------------------------------------------------------------------


async def test_enumerate_sessions_replays_real_stdout(recorded, monkeypatch):
    case = recorded["enumerate_sessions"]["real"]
    monkeypatch.setattr(sessions_mod, "run_tmux", make_player(case["tape"]))
    names = await sessions_mod.enumerate_sessions()
    got = {
        "names": names,
        "activity": sessions_mod.get_session_activity(),
        "created": sessions_mod.get_session_created_times(),
        "cwds": sessions_mod.get_session_cwds(),
    }
    assert canon(got) == canon(case["expected"])


async def test_enumerate_sessions_no_server_returns_empty(recorded, monkeypatch):
    case = recorded["enumerate_sessions"]["no_server"]
    monkeypatch.setattr(sessions_mod, "run_tmux", make_player(case["tape"]))
    names = await sessions_mod.enumerate_sessions()
    got = {
        "names": names,
        "activity": sessions_mod.get_session_activity(),
        "created": sessions_mod.get_session_created_times(),
        "cwds": sessions_mod.get_session_cwds(),
    }
    assert canon(got) == canon(case["expected"])


async def test_enumerate_sessions_malformed_line_tolerances(recorded, monkeypatch):
    """The sessions.py:452-490 tolerances, on real-derived stdout.

    Each variant is the REAL recorded stdout minimally mutated (mutation
    documented in the fixture) — tmux cannot be made to emit malformed
    output on demand, so derivation-from-real is the honest form here.
    """
    for case in recorded["enumerate_sessions"]["derived"]:
        stdout_value = case["stdout"]

        async def canned(*args: str, _v=stdout_value) -> str:
            return _v

        monkeypatch.setattr(sessions_mod, "run_tmux", canned)
        names = await sessions_mod.enumerate_sessions()
        got = {
            "names": names,
            "activity": sessions_mod.get_session_activity(),
            "created": sessions_mod.get_session_created_times(),
            "cwds": sessions_mod.get_session_cwds(),
        }
        assert canon(got) == canon(case["expected"]), case["description"]


# ---------------------------------------------------------------------------
# observe: probe_tmux_epoch
# ---------------------------------------------------------------------------


async def test_probe_tmux_epoch_replays_live_server(recorded, monkeypatch):
    case = recorded["probe_tmux_epoch"]["live"]
    monkeypatch.setattr(sessions_mod, "run_tmux", make_player(case["tape"]))

    real_stat = os.stat
    socket_path = case["expected"]["socket_path"]

    def fake_stat(path, *args, **kwargs):
        if str(path) == socket_path:
            # A real os.stat_result whose st_ino is the recorded inode.
            return os.stat_result(
                (stat_module.S_IFSOCK, case["inode"], 0, 1, 0, 0, 0, 0, 0, 0)
            )
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", fake_stat)
    result = await sessions_mod.probe_tmux_epoch()
    assert canon(result) == canon(case["expected"])


async def test_probe_tmux_epoch_no_server_returns_none(recorded, monkeypatch):
    case = recorded["probe_tmux_epoch"]["no_server"]
    monkeypatch.setattr(sessions_mod, "run_tmux", make_player(case["tape"]))
    assert await sessions_mod.probe_tmux_epoch() is None


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------


async def test_capture_pane_replays(recorded, monkeypatch):
    case = recorded["capture"]["capture_pane"]
    monkeypatch.setattr(sessions_mod, "run_tmux", make_player(case["tape"]))
    assert await sessions_mod.capture_pane("alpha") == case["expected"]


async def test_capture_pane_metadata_replays(recorded, monkeypatch):
    case = recorded["capture"]["capture_pane_metadata"]
    monkeypatch.setattr(sessions_mod, "run_tmux", make_player(case["tape"]))
    result = await sessions_mod.capture_pane_metadata("alpha")
    assert list(result) == case["expected"]


async def test_capture_pane_window_replays(recorded, monkeypatch):
    case = recorded["capture"]["capture_pane_window"]
    monkeypatch.setattr(sessions_mod, "run_tmux", make_player(case["tape"]))
    result = await sessions_mod.capture_pane_window(
        "alpha", case["args"]["s"], case["args"]["e"]
    )
    assert list(result) == case["expected"]


# ---------------------------------------------------------------------------
# bells: poll_bell_flag (incl. the background-window incident)
# ---------------------------------------------------------------------------


async def test_poll_bell_flag_pre_bell_is_false(recorded, monkeypatch):
    case = recorded["poll_bell_flag"]["pre_bell"]
    monkeypatch.setattr(bells_mod, "run_tmux", make_player(case["tape"]))
    assert await bells_mod.poll_bell_flag("beta") is case["expected"] is False


async def test_poll_bell_flag_sees_background_window_bell(recorded, monkeypatch):
    """The multi-window incident (bells.py:45-56): the bell fired in a real
    NON-active window; list-windows enumerates every window, so the recorded
    stdout has the belling window's '1' even though the active window is '0'.
    """
    case = recorded["poll_bell_flag"]["background_window_bell"]
    flags = case["tape"][0]["stdout"].split()
    assert "1" in flags and "0" in flags, "fixture must show the incident shape"
    monkeypatch.setattr(bells_mod, "run_tmux", make_player(case["tape"]))
    assert await bells_mod.poll_bell_flag("beta") is case["expected"] is True


# ---------------------------------------------------------------------------
# presence: update_manifest — the §8.2 centerpiece
# ---------------------------------------------------------------------------


def test_update_manifest_replays_every_recorded_cycle(recorded):
    """Replay every real (manifest, epoch, live_names, cwds, now) tuple and
    assert (manifest, changed) byte-identical to the recorded baseline.

    Covers §8.2's four named cases (first-run adoption, same-epoch tombstone,
    cold-start freeze, epoch-None no-op) plus the same-epoch add/quiet cycles
    and the §13.3 PRE-S4 unknown-key baseline.
    """
    for case in recorded["update_manifest"]["cases"]:
        inputs = copy.deepcopy(case["inputs"])
        result, changed = manifest_mod.update_manifest(
            inputs["manifest"],
            inputs["epoch_now"],
            inputs["live_names"],
            now=inputs["now"],
            cwds=inputs["cwds"],
        )
        assert canon(result) == canon(case["expected"]["manifest"]), case["description"]
        assert changed == case["expected"]["changed"], case["description"]


def test_update_manifest_epoch_none_is_a_true_noop(recorded):
    """The epoch_now-None branch returns the manifest UNCHANGED — beyond
    byte-equality, nothing may be structurally added or dropped."""
    case = next(
        c
        for c in recorded["update_manifest"]["cases"]
        if c["inputs"]["epoch_now"] is None
    )
    manifest = copy.deepcopy(case["inputs"]["manifest"])
    result, changed = manifest_mod.update_manifest(
        manifest, None, [], now=case["inputs"]["now"], cwds={}
    )
    assert changed is False
    assert canon(result) == canon(case["inputs"]["manifest"])


def test_pre_s4_baseline_unknown_toplevel_keys_are_dropped(recorded):
    """Pins TODAY's closed-key-set rebuild (manifest.py:335-341, :371-377,
    :419-425): app-owned top-level keys (rename_in_flight, app_extra) are
    dropped on a changed cycle.

    S4 (plan §13.3) deliberately CHANGES this contract to round-trip unknown
    keys verbatim. When S4 lands, this test and its fixture case are
    re-recorded together; if this test goes red for any other reason, a move
    has changed manifest behavior it must not change.
    """
    case = next(
        c
        for c in recorded["update_manifest"]["cases"]
        if c["description"].startswith("PRE-S4 BASELINE")
    )
    inputs = copy.deepcopy(case["inputs"])
    assert "app_extra" in inputs["manifest"], "fixture must carry the unknown key"
    assert inputs["manifest"]["rename_in_flight"] is not None
    result, changed = manifest_mod.update_manifest(
        inputs["manifest"],
        inputs["epoch_now"],
        inputs["live_names"],
        now=inputs["now"],
        cwds=inputs["cwds"],
    )
    assert changed is True
    assert "app_extra" not in result
    assert "rename_in_flight" not in result
    assert canon(result) == canon(case["expected"]["manifest"])


def test_cold_start_freezes_lost_sessions_verbatim(recorded):
    """The cold-start branch (manifest.py:390-394): a lost session's entry —
    including its observed real cwd — freezes into pending_restore verbatim,
    and does NOT survive un-frozen in `sessions`."""
    case = next(
        c
        for c in recorded["update_manifest"]["cases"]
        if c["description"].startswith("cold start")
    )
    inputs = case["inputs"]
    expected = case["expected"]["manifest"]
    frozen = expected["pending_restore"]["sessions"]
    lost = [n for n in inputs["manifest"]["sessions"] if n not in inputs["live_names"]]
    assert lost, "fixture must actually lose a session across the cold start"
    for name in lost:
        assert canon(frozen[name]) == canon(inputs["manifest"]["sessions"][name])
        assert name not in expected["sessions"]
    assert expected["pending_restore"]["lost_epoch"] == inputs["manifest"]["epoch"]


# ---------------------------------------------------------------------------
# presence: manifest I/O
# ---------------------------------------------------------------------------


def test_load_manifest_absent_file(recorded, tmp_path, monkeypatch):
    monkeypatch.setattr(manifest_mod, "MANIFEST_PATH", tmp_path / "sessions.json")
    assert canon(manifest_mod.load_manifest()) == canon(
        recorded["manifest_io"]["load_absent_file"]["expected"]
    )


def test_manifest_io_roundtrips_recorded_real_file(recorded, tmp_path, monkeypatch):
    """The exact bytes save_manifest() wrote for a REAL adopted manifest at
    record time load back to the recorded dict, and re-saving reproduces the
    same on-disk content."""
    path = tmp_path / "sessions.json"
    monkeypatch.setattr(manifest_mod, "MANIFEST_PATH", path)
    path.write_text(recorded["manifest_io"]["saved_file_content"], encoding="utf-8")
    loaded = manifest_mod.load_manifest()
    assert canon(loaded) == canon(recorded["manifest_io"]["reload_expected"])
    manifest_mod.save_manifest(loaded)
    assert canon(json.loads(path.read_text(encoding="utf-8"))) == canon(loaded)


def test_restore_helpers_replay(recorded):
    rh = recorded["restore_helpers"]
    plan_case = rh["compute_restore_plan"]
    assert (
        manifest_mod.compute_restore_plan(
            copy.deepcopy(plan_case["inputs"]["manifest"]),
            plan_case["inputs"]["live_names"],
        )
        == plan_case["expected"]
    )
    mr_case = rh["mark_restored"]
    result = manifest_mod.mark_restored(
        copy.deepcopy(mr_case["inputs"]["manifest"]),
        set(mr_case["inputs"]["restored_names"]),
    )
    assert canon(result) == canon(mr_case["expected"])
    cwd_case = rh["get_restore_cwd"]
    assert (
        manifest_mod.get_restore_cwd(
            cwd_case["inputs"]["manifest"], cwd_case["inputs"]["name"]
        )
        == cwd_case["expected"]
    )


# ---------------------------------------------------------------------------
# proc: tmux_env construction (the seam S2 inverts — sessions.py:294)
# ---------------------------------------------------------------------------


def test_tmux_env_with_socket_dir_overrides_and_pops_tmux(recorded, monkeypatch):
    socket_dir = recorded["tmux_env"]["socket_dir"]
    monkeypatch.setattr(
        sessions_mod, "load_settings", lambda: {"tmux_socket_dir": socket_dir}
    )
    monkeypatch.setenv("TMUX", "/tmp/fake-ambient-tmux-socket,123,0")
    env = sessions_mod.tmux_env()
    assert env is not None
    assert env["TMUX_TMPDIR"] == socket_dir
    assert "TMUX" not in env
    # Everything else is inherited unchanged from os.environ.
    expected = dict(os.environ)
    expected["TMUX_TMPDIR"] = socket_dir
    expected.pop("TMUX", None)
    assert env == expected


def test_tmux_env_unset_returns_none(recorded, monkeypatch):
    monkeypatch.setattr(sessions_mod, "load_settings", lambda: {"tmux_socket_dir": ""})
    assert sessions_mod.tmux_env() is recorded["tmux_env"]["expected_when_unset"]


# ---------------------------------------------------------------------------
# keys: the Sender/SendPolicy precursor surface (terminal_input.py)
# ---------------------------------------------------------------------------


def test_send_text_argv_replays(recorded):
    for case in recorded["keys"]["send_text"]:
        assert (
            ti_mod.build_send_text_argv(case["name"], case["text"]) == case["expected"]
        )


def test_send_key_argv_replays(recorded):
    for case in recorded["keys"]["send_key"]:
        assert ti_mod.build_send_key_argv(case["name"], case["key"]) == case["expected"]


def test_allowlist_fence_replays(recorded):
    for case in recorded["keys"]["allowlist"]:
        assert (
            ti_mod.session_matches_allowlist(case["name"], case["patterns"])
            is case["expected"]
        ), case


def test_input_allowed_fence_replays(recorded):
    for case in recorded["keys"]["input_allowed"]:
        assert (
            ti_mod.input_allowed_for_session(case["name"], case["settings"])
            is case["expected"]
        ), case


def test_keys_constants_unchanged(recorded):
    consts = recorded["keys"]["constants"]
    assert sorted(ti_mod.ALLOWED_KEYS) == consts["ALLOWED_KEYS"]
    assert ti_mod.MAX_TEXT_BYTES == consts["MAX_TEXT_BYTES"]
    assert ti_mod.MAX_KEYS == consts["MAX_KEYS"]


# ---------------------------------------------------------------------------
# ttyd AF_UNIX lifecycle: naming replay + real-socket liveness/validation
# ---------------------------------------------------------------------------


def test_ttyd_socket_naming_replays(recorded):
    t = recorded["ttyd"]
    assert ttyd_mod.SOCKET_SUFFIX == t["SOCKET_SUFFIX"] == ".sock"
    assert ttyd_mod.SUN_PATH_BUDGET == t["SUN_PATH_BUDGET"]
    assert ttyd_mod.SOCKET_BASENAME_LEN == t["SOCKET_BASENAME_LEN"]
    for name, basename in t["socket_basenames"].items():
        got = ttyd_mod.socket_path_for(name).name
        assert got == basename
        # The SOCKET_SUFFIX fence property (ttyd.py:29-43): a non-.sock path
        # makes ttyd silently fall back to TCP INADDR_ANY:7681. Every derived
        # path MUST carry the suffix.
        assert got.endswith(ttyd_mod.SOCKET_SUFFIX)


def test_socket_is_live_against_real_af_unix_socket(short_socket_dir):
    """Live-hermetic (not a replay): liveness is a real connect(), which JSON
    cannot capture. A REAL listener answers True; the SAME path with the
    listener closed (the stale-file-after-SIGKILL shape) answers False; an
    absent path answers False."""
    path = short_socket_dir / "mx-differential.sock"
    assert ttyd_mod.socket_is_live(path) is False  # absent
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(path))
        listener.listen(1)
        assert ttyd_mod.socket_is_live(path) is True
    finally:
        listener.close()
    assert path.exists()  # the stale file survives...
    assert ttyd_mod.socket_is_live(path) is False  # ...but is not "live"


def test_validate_socket_dir_real_checks(short_socket_dir):
    """Live-hermetic: validate_socket_dir against real directories."""
    good = short_socket_dir / "ok"
    ttyd_mod.validate_socket_dir(good)  # must not raise; includes a real bind

    link = short_socket_dir / "link"
    link.symlink_to(good)
    with pytest.raises(ttyd_mod.TtydSocketDirError, match="symlink"):
        ttyd_mod.validate_socket_dir(link)

    deep = short_socket_dir / ("d" * (ttyd_mod.SUN_PATH_BUDGET + 1))
    with pytest.raises(ttyd_mod.TtydSocketDirError, match="sun_path"):
        ttyd_mod.validate_socket_dir(deep)

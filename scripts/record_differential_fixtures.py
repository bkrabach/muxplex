#!/usr/bin/env python3
"""Record real-input fixtures for the differential harness (extraction stage S0).

See docs/plans/2026-08-08-tmux-lib-extraction-plan.md §8.2: the extraction's
safety story is a harness that replays REAL recorded inputs through the code
before and after each move, asserting byte-identical behavior. This script is
the recording half. It:

  1. Creates a scratch TMUX_TMPDIR under /tmp (short path — sun_path budget)
     and a scratch settings.json whose ``tmux_socket_dir`` points at it, then
     repoints ``muxplex.settings.SETTINGS_PATH`` there. Every tmux call then
     flows through the REAL production path (``tmux_env()`` -> ``run_tmux()``)
     against an ISOLATED tmux server. The ambient tmux server, the live
     muxplex on port 8088, and the user's real config are never touched
     (AGENTS.md: "NEVER broad-kill", "isolated -L socket / TMUX_TMPDIR").
  2. Drives that real server through the lifecycle §8.2 names: sessions
     created, output produced, a bell fired in a BACKGROUND window (the
     multi-window incident), a session killed (tombstone), the server killed
     (epoch death), a new server started (cold start).
  3. Records, per case, the raw tmux argv + stdout/stderr that actually
     crossed the subprocess boundary, plus each covered function's inputs and
     its output as computed by the CURRENT code — the baseline every later
     stage diffs against.
  4. Writes muxplex/tests/fixtures/differential/recorded.json, which
     muxplex/tests/test_differential_harness.py replays.

Teardown kills ONLY the scratch server, socket-scoped via the scratch
TMUX_TMPDIR (never a bare ``tmux kill-server``), and removes the scratch dir.

Run from the repo root:

    uv run python scripts/record_differential_fixtures.py

Honest scope notes (§8.2 discipline):

- Every ``list-sessions`` / ``display-message`` / ``capture-pane`` /
  ``list-windows`` stdout in the fixture is genuinely produced by a real tmux
  server. The malformed-line tolerance cases for ``enumerate_sessions()``
  (sessions.py:452-490) cannot be produced by a healthy tmux on demand, so
  they are DERIVED from the real recorded stdout by a minimal, documented
  mutation and marked ``"derived_from_real": true`` in the fixture.
- ``update_manifest()`` inputs are the real ``(manifest, epoch, live_names,
  cwds)`` tuples observed from the live scratch server, fed forward cycle to
  cycle exactly as main.py's poll loop would.
"""

from __future__ import annotations

import asyncio
import copy
import datetime
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

FIXTURE_PATH = (
    REPO_ROOT / "muxplex" / "tests" / "fixtures" / "differential" / "recorded.json"
)


# ---------------------------------------------------------------------------
# Tape: record the raw traffic that crosses the run_tmux subprocess boundary
# ---------------------------------------------------------------------------


class Tape:
    """Captures (argv, stdout | error) pairs while active."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []
        self.active = False

    def start(self) -> None:
        self.entries = []
        self.active = True

    def stop(self) -> list[dict[str, Any]]:
        self.active = False
        return self.entries


async def main() -> None:
    import muxplex.settings as settings_mod

    scratch = Path(tempfile.mkdtemp(prefix="mx-diff-rec-", dir="/tmp"))
    tmux_dir = scratch / "tmux"
    tmux_dir.mkdir()
    settings_path = scratch / "settings.json"
    settings_path.write_text(
        json.dumps({"tmux_socket_dir": str(tmux_dir)}), encoding="utf-8"
    )
    # Point the production settings loader at the scratch config. From here on
    # every run_tmux() resolves TMUX_TMPDIR to the scratch dir (and pops TMUX),
    # so nothing below can reach the ambient tmux server.
    settings_mod.SETTINGS_PATH = settings_path

    import muxplex.bells as bells_mod
    import muxplex.manifest as manifest_mod
    import muxplex.sessions as sessions_mod
    import muxplex.terminal_input as ti_mod
    import muxplex.ttyd as ttyd_mod

    real_run_tmux = sessions_mod.run_tmux
    tape = Tape()

    async def recording_run_tmux(*args: str) -> str:
        try:
            out = await real_run_tmux(*args)
        except RuntimeError as exc:
            if tape.active:
                tape.entries.append({"args": list(args), "error": str(exc)})
            raise
        if tape.active:
            tape.entries.append({"args": list(args), "stdout": out})
        return out

    sessions_mod.run_tmux = recording_run_tmux  # type: ignore[assignment]
    bells_mod.run_tmux = recording_run_tmux  # type: ignore[assignment]

    fixture: dict[str, Any] = {
        "_meta": {
            "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "tmux_version": subprocess.run(  # noqa: ASYNC221 - one-shot metadata read
                ["tmux", "-V"], capture_output=True, text=True, check=True
            ).stdout.strip(),
            "platform": platform.platform(),
            "recorder": "scripts/record_differential_fixtures.py",
            "plan": "docs/plans/2026-08-08-tmux-lib-extraction-plan.md §8.2",
        }
    }

    async def observe(taped: bool) -> dict[str, Any]:
        """Run a real enumerate_sessions() and capture its raw stdout + parses."""
        if taped:
            tape.start()
        names = await sessions_mod.enumerate_sessions()
        entries = tape.stop() if taped else []
        return {
            "tape": entries,
            "names": names,
            "activity": sessions_mod.get_session_activity(),
            "created": sessions_mod.get_session_created_times(),
            "cwds": sessions_mod.get_session_cwds(),
        }

    try:
        # ------------------------------------------------------------------
        # Bring up the isolated server with two real sessions.
        # ------------------------------------------------------------------
        await real_run_tmux("new-session", "-d", "-s", "alpha", "-x", "200", "-y", "50")
        await real_run_tmux("new-session", "-d", "-s", "beta", "-x", "200", "-y", "50")
        await real_run_tmux("new-window", "-t", "beta")  # beta gets 2 windows
        # Window indexes are whatever the user's tmux.conf makes them
        # (base-index) -- query the REAL indexes rather than assuming 0/1.
        beta_windows = (
            await real_run_tmux("list-windows", "-t", "beta", "-F", "#{window_index}")
        ).split()
        assert len(beta_windows) == 2, beta_windows
        await real_run_tmux("select-window", "-t", f"beta:{beta_windows[0]}")
        await real_run_tmux("set-window-option", "-g", "monitor-bell", "on")

        # ------------------------------------------------------------------
        # observe: enumerate_sessions against the real server
        # ------------------------------------------------------------------
        obs1 = await observe(taped=True)
        assert sorted(obs1["names"]) == ["alpha", "beta"], obs1["names"]

        # Derived malformed-line variants (base is the REAL stdout above).
        real_stdout = obs1["tape"][0]["stdout"]
        derived_cases = []
        lines = [ln for ln in real_stdout.splitlines() if ln.strip()]
        first = lines[0]
        name0 = first.split("\t")[0]
        # (a) fewer tabs than expected: name only
        derived_cases.append(
            {
                "description": "real line truncated to name-only (fewer than 3 tabs)",
                "derived_from_real": True,
                "mutation": "kept only the #{session_name} field of a real line",
                "stdout": name0 + "\n",
            }
        )
        # (b) non-numeric activity field on a real line
        parts = first.split("\t")
        mangled = "\t".join([parts[0], "not-a-number", *parts[2:]])
        derived_cases.append(
            {
                "description": "real line with window_activity made non-numeric",
                "derived_from_real": True,
                "mutation": "replaced #{window_activity} with 'not-a-number'",
                "stdout": mangled + "\n",
            }
        )
        # (c) blank lines interleaved with real lines
        derived_cases.append(
            {
                "description": "real stdout with a blank line interleaved",
                "derived_from_real": True,
                "mutation": "inserted one blank line between real lines",
                "stdout": lines[0] + "\n\n" + "\n".join(lines[1:]) + "\n",
            }
        )
        # Compute current-code baselines for the derived stdout by replaying
        # through the real parser (patched run_tmux returning the variant).
        derived_with_baselines = []
        for case in derived_cases:
            stdout_value = case["stdout"]

            async def canned(*args: str, _v=stdout_value) -> str:
                return _v

            sessions_mod.run_tmux = canned  # type: ignore[assignment]
            names = await sessions_mod.enumerate_sessions()
            derived_with_baselines.append(
                {
                    **case,
                    "expected": {
                        "names": names,
                        "activity": sessions_mod.get_session_activity(),
                        "created": sessions_mod.get_session_created_times(),
                        "cwds": sessions_mod.get_session_cwds(),
                    },
                }
            )
        sessions_mod.run_tmux = recording_run_tmux  # type: ignore[assignment]
        # Re-prime caches from the real server (canned replay clobbered them).
        await observe(taped=False)

        fixture["enumerate_sessions"] = {
            "real": {
                "tape": obs1["tape"],
                "expected": {
                    "names": obs1["names"],
                    "activity": obs1["activity"],
                    "created": obs1["created"],
                    "cwds": obs1["cwds"],
                },
            },
            "derived": derived_with_baselines,
        }

        # ------------------------------------------------------------------
        # probe_tmux_epoch: live server
        # ------------------------------------------------------------------
        tape.start()
        epoch1 = await sessions_mod.probe_tmux_epoch()
        epoch1_tape = tape.stop()
        assert epoch1 is not None
        fixture["probe_tmux_epoch"] = {
            "live": {"tape": epoch1_tape, "inode": epoch1["inode"], "expected": epoch1}
        }

        # ------------------------------------------------------------------
        # capture: put real output into alpha's pane, then capture it
        # ------------------------------------------------------------------
        marker = "muxplex-differential-marker"
        await real_run_tmux("send-keys", "-t", "alpha", f"echo {marker}", "Enter")
        deadline = time.time() + 10
        while time.time() < deadline:
            text = await real_run_tmux(
                "capture-pane", "-e", "-p", "-t", "alpha", "-S", "-30"
            )
            if text.count(marker) >= 2:  # echoed command + its output
                break
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("marker never appeared in alpha's pane")

        tape.start()
        cap = await sessions_mod.capture_pane("alpha")
        cap_tape = tape.stop()

        tape.start()
        meta = await sessions_mod.capture_pane_metadata("alpha")
        meta_tape = tape.stop()

        tape.start()
        window = await sessions_mod.capture_pane_window("alpha", -30, None)
        window_tape = tape.stop()

        fixture["capture"] = {
            "capture_pane": {"tape": cap_tape, "expected": cap},
            "capture_pane_metadata": {"tape": meta_tape, "expected": list(meta)},
            "capture_pane_window": {
                "tape": window_tape,
                "args": {"s": -30, "e": None},
                "expected": list(window),
            },
        }

        # ------------------------------------------------------------------
        # bells: poll_bell_flag before/after a REAL bell in a BACKGROUND
        # window (the multi-window incident, bells.py:45-56)
        # ------------------------------------------------------------------
        tape.start()
        pre = await bells_mod.poll_bell_flag("beta")
        pre_tape = tape.stop()
        assert pre is False

        await real_run_tmux(
            "send-keys", "-t", f"beta:{beta_windows[1]}", "printf '\\a'", "Enter"
        )
        deadline = time.time() + 10
        while time.time() < deadline:
            flags = await real_run_tmux(
                "list-windows", "-t", "beta", "-F", "#{window_bell_flag}"
            )
            if "1" in flags.split():
                break
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("bell flag never set on beta")

        tape.start()
        post = await bells_mod.poll_bell_flag("beta")
        post_tape = tape.stop()
        assert post is True

        fixture["poll_bell_flag"] = {
            "pre_bell": {"tape": pre_tape, "expected": pre},
            "background_window_bell": {"tape": post_tape, "expected": post},
        }

        # ------------------------------------------------------------------
        # presence: update_manifest() fed the REAL observed tuples, cycle by
        # cycle, exactly as the poll loop would (§8.2's four named cases plus
        # the same-epoch add and the §13.3 unknown-key baseline)
        # ------------------------------------------------------------------
        manifest_cases: list[dict[str, Any]] = []

        def run_case(
            description: str,
            manifest: dict[str, Any],
            epoch_now: dict[str, Any] | None,
            live_names: list[str],
            cwds: dict[str, str],
            now: float,
            note: str | None = None,
        ) -> dict[str, Any]:
            inputs = {
                "manifest": copy.deepcopy(manifest),
                "epoch_now": copy.deepcopy(epoch_now),
                "live_names": list(live_names),
                "cwds": dict(cwds),
                "now": now,
            }
            result, changed = manifest_mod.update_manifest(
                copy.deepcopy(manifest),
                copy.deepcopy(epoch_now),
                list(live_names),
                now=now,
                cwds=dict(cwds),
            )
            case: dict[str, Any] = {
                "description": description,
                "inputs": inputs,
                "expected": {"manifest": result, "changed": changed},
            }
            if note:
                case["note"] = note
            manifest_cases.append(case)
            return result

        # Case: first-run adoption (empty manifest from a real absent file)
        manifest_mod.MANIFEST_PATH = scratch / "sessions.json"
        m0 = manifest_mod.load_manifest()
        manifest_io: dict[str, Any] = {
            "load_absent_file": {"expected": m0},
        }
        fixture["manifest_io"] = manifest_io
        t1 = time.time()
        m1 = run_case(
            "first-run adoption: empty manifest + live server",
            m0,
            epoch1,
            obs1["names"],
            obs1["cwds"],
            t1,
        )

        # Real save/load round trip of the adopted manifest
        manifest_mod.save_manifest(m1)
        saved_bytes = manifest_mod.MANIFEST_PATH.read_text(encoding="utf-8")
        reloaded = manifest_mod.load_manifest()
        manifest_io["saved_file_content"] = saved_bytes
        manifest_io["reload_expected"] = reloaded

        # Case: same-epoch quiet cycle (no structural change)
        m1b = run_case(
            "same-epoch quiet cycle: nothing changed",
            m1,
            epoch1,
            obs1["names"],
            obs1["cwds"],
            time.time(),
        )

        # Case: same-epoch new session appears (create gamma for real)
        await real_run_tmux("new-session", "-d", "-s", "gamma", "-x", "200", "-y", "50")
        obs2 = await observe(taped=False)
        assert "gamma" in obs2["names"]
        m2 = run_case(
            "same-epoch: new live session (gamma) recorded",
            m1b,
            epoch1,
            obs2["names"],
            obs2["cwds"],
            time.time(),
        )

        # Case: same-epoch tombstone (kill gamma for real, exact target)
        await real_run_tmux("kill-session", "-t", "=gamma")
        obs3 = await observe(taped=False)
        assert "gamma" not in obs3["names"]
        t4 = time.time()
        m3 = run_case(
            "same-epoch tombstone: gamma killed against a live, "
            "identity-matched server",
            m2,
            epoch1,
            obs3["names"],
            obs3["cwds"],
            t4,
        )

        # Case (§13.3 baseline, the bed S4's contract change will be proven
        # in): app-owned top-level keys are DROPPED by the closed-key-set
        # rebuild on any changed cycle (manifest.py:335-341/:371-377/:419-425).
        m3x = manifest_mod.start_rename_journal(m3, "alpha", "alpha2", now=t4)
        m3x["app_extra"] = {"origin": "recorded-baseline", "kept": False}
        run_case(
            "PRE-S4 BASELINE: unknown top-level keys (rename_in_flight, "
            "app_extra) dropped on a changed same-epoch cycle",
            m3x,
            epoch1,
            obs2["names"],
            obs2["cwds"],
            time.time(),
            note=(
                "S4 (plan §13.3) will change this contract to round-trip "
                "unknown top-level keys verbatim. When S4 lands, THIS case's "
                "expectation is deliberately re-recorded; every other case "
                "must stay byte-identical."
            ),
        )

        # Case: no tmux server at all -> epoch_now None -> no-op
        await real_run_tmux("kill-server")
        await asyncio.sleep(0.3)
        tape.start()
        epoch_none = await sessions_mod.probe_tmux_epoch()
        none_tape = tape.stop()
        assert epoch_none is None
        fixture["probe_tmux_epoch"]["no_server"] = {
            "tape": none_tape,
            "expected": None,
        }
        tape.start()
        empty_names = await sessions_mod.enumerate_sessions()
        empty_tape = tape.stop()
        assert empty_names == []
        fixture["enumerate_sessions"]["no_server"] = {
            "tape": empty_tape,
            "expected": {"names": [], "activity": {}, "created": {}, "cwds": {}},
        }
        run_case(
            "epoch_now is None: knowledge unavailable, manifest returned unchanged",
            m3,
            None,
            [],
            {},
            time.time(),
        )

        # Case: cold start -- NEW server (new pid/inode), only alpha comes
        # back; beta freezes verbatim into pending_restore
        await real_run_tmux("new-session", "-d", "-s", "alpha", "-x", "200", "-y", "50")
        epoch2 = await sessions_mod.probe_tmux_epoch()
        assert epoch2 is not None and not manifest_mod._same_epoch(epoch1, epoch2)
        obs4 = await observe(taped=False)
        assert obs4["names"] == ["alpha"]
        m4 = run_case(
            "cold start: different epoch; beta frozen into pending_restore "
            "verbatim; live entries rebuilt fresh",
            m3,
            epoch2,
            obs4["names"],
            obs4["cwds"],
            time.time(),
        )
        assert m4["pending_restore"] and "beta" in m4["pending_restore"]["sessions"]

        fixture["update_manifest"] = {"cases": manifest_cases}

        # Restore-plan helpers against the real cold-start result
        plan = manifest_mod.compute_restore_plan(m4, obs4["names"])
        restored = manifest_mod.mark_restored(m4, {"beta"})
        fixture["restore_helpers"] = {
            "compute_restore_plan": {
                "inputs": {"manifest": m4, "live_names": obs4["names"]},
                "expected": plan,
            },
            "mark_restored": {
                "inputs": {"manifest": m4, "restored_names": ["beta"]},
                "expected": restored,
            },
            "get_restore_cwd": {
                "inputs": {"manifest": m4, "name": "beta"},
                "expected": manifest_mod.get_restore_cwd(m4, "beta"),
            },
        }

        # ------------------------------------------------------------------
        # proc: tmux_env() construction (the injected-config seam S2 inverts)
        # ------------------------------------------------------------------
        fixture["tmux_env"] = {
            "socket_dir": str(tmux_dir),
            "expected_when_unset": None,
        }

        # ------------------------------------------------------------------
        # keys (the Sender/SendPolicy precursor): argv builders + fence,
        # applied to the session names genuinely observed on this server and
        # the hostile payload from the recorded /input incident test
        # ------------------------------------------------------------------
        observed_names = obs2["names"]  # alpha, beta, gamma -- all real
        hostile = "; rm -rf / && $(reboot) `id` | tee /etc/passwd"
        keys_cases: dict[str, Any] = {
            "send_text": [
                {
                    "name": n,
                    "text": t,
                    "expected": ti_mod.build_send_text_argv(n, t),
                }
                for n in observed_names
                for t in [f"echo {marker}", hostile, "-leading-dash text"]
            ],
            "send_key": [
                {
                    "name": observed_names[0],
                    "key": k,
                    "expected": ti_mod.build_send_key_argv(observed_names[0], k),
                }
                for k in sorted(ti_mod.ALLOWED_KEYS)
            ],
            "allowlist": [
                {
                    "name": n,
                    "patterns": p,
                    "expected": ti_mod.session_matches_allowlist(n, p),
                }
                for n in observed_names
                for p in [
                    [],
                    ["*"],
                    ["alpha"],
                    ["ALPHA"],
                    ["amplifier-*"],
                    ["bet*"],
                    [None, 42, "alpha"],
                ]
            ],
            "input_allowed": [
                {
                    "name": n,
                    "settings": s,
                    "expected": ti_mod.input_allowed_for_session(n, s),
                }
                for n in observed_names
                for s in [
                    {},
                    {"input_enabled": False, "input_allowed_sessions": ["*"]},
                    {"input_enabled": "true", "input_allowed_sessions": ["*"]},
                    {"input_enabled": True, "input_allowed_sessions": []},
                    {"input_enabled": True, "input_allowed_sessions": ["*"]},
                    {"input_enabled": True, "input_allowed_sessions": "alpha"},
                ]
            ],
            "constants": {
                "ALLOWED_KEYS": sorted(ti_mod.ALLOWED_KEYS),
                "MAX_TEXT_BYTES": ti_mod.MAX_TEXT_BYTES,
                "MAX_KEYS": ti_mod.MAX_KEYS,
            },
        }
        fixture["keys"] = keys_cases

        # ------------------------------------------------------------------
        # ttyd AF_UNIX naming: hashed fixed-width basenames for the real
        # session names, and the SOCKET_SUFFIX fence constant (ttyd.py:29-43)
        # ------------------------------------------------------------------
        fixture["ttyd"] = {
            "SOCKET_SUFFIX": ttyd_mod.SOCKET_SUFFIX,
            "SUN_PATH_BUDGET": ttyd_mod.SUN_PATH_BUDGET,
            "SOCKET_BASENAME_LEN": ttyd_mod.SOCKET_BASENAME_LEN,
            "socket_basenames": {
                n: ttyd_mod.socket_path_for(n).name for n in observed_names
            },
        }

    finally:
        # Socket-scoped teardown: this kill-server resolves through the
        # scratch settings' tmux_socket_dir -- it can only reach the scratch
        # server. Never a bare kill against the ambient server.
        try:
            await real_run_tmux("kill-server")
        except (RuntimeError, FileNotFoundError):
            pass
        sessions_mod.run_tmux = real_run_tmux  # type: ignore[assignment]
        bells_mod.run_tmux = real_run_tmux  # type: ignore[assignment]
        shutil.rmtree(scratch, ignore_errors=True)

    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(
        json.dumps(fixture, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {FIXTURE_PATH} ({FIXTURE_PATH.stat().st_size} bytes)")


if __name__ == "__main__":
    asyncio.run(main())

"""
Integration tests for the session-presence manifest (manifest.py +
sessions.probe_tmux_epoch), using REAL isolated tmux servers.

These are the tests that prove the actual point of
SESSION_PERSISTENCE_DESIGN.md's "v1a -- record only" milestone: a manifest
that survives an unplanned tmux server death, and correctly tells that death
apart from (a) muxplex simply restarting with tmux untouched, and (b) a
session the user deliberately killed while the server stayed up.

Each test gets its OWN uniquely-named tmux socket (``tmux -L <name>``), never
the default socket and never the live muxplex socket -- per AGENTS.md's
"NEVER broad-kill by process name on a host running a live muxplex" and
"Running a second instance on one box" sections. No test in this file ever
touches a bare/default tmux server.

Run with:
    pytest -m integration -v

Default test run (unit tests only, per AGENTS.md):
    pytest -v
"""

import asyncio
import subprocess
import uuid
from unittest.mock import patch

import pytest

from muxplex.manifest import load_manifest, save_manifest, update_manifest
from muxplex.sessions import probe_tmux_epoch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def tmux(socket: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a tmux command scoped to *socket* (``tmux -L <socket> ...``)."""
    return subprocess.run(
        ["tmux", "-L", socket, *args],
        capture_output=True,
        text=True,
        check=check,
    )


def unique_socket(prefix: str) -> str:
    """A unique, disposable tmux socket name for this test only."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def probe_on_socket(socket: str):
    """probe_tmux_epoch(), routed through *socket* instead of the default.

    The seam targets muxplex.tmux.observe -- the module where
    probe_tmux_epoch() has resolved run_tmux since the S1 extraction moved
    it there (patching the old sessions re-export would be invisible to it).
    """
    import muxplex.tmux.observe as observe_mod

    async def patched_run_tmux(*args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "tmux",
            "-L",
            socket,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(stderr_bytes.decode("utf-8", errors="replace"))
        return stdout_bytes.decode("utf-8", errors="replace")

    with patch.object(observe_mod, "run_tmux", side_effect=patched_run_tmux):
        return await probe_tmux_epoch()


async def live_names_on_socket(socket: str) -> list[str]:
    """The current list of session names on *socket* (empty if no server)."""
    result = tmux(socket, "list-sessions", "-F", "#{session_name}", check=False)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


@pytest.fixture(autouse=True)
def redirect_manifest_path(tmp_path, monkeypatch):
    """Every test gets its own manifest file -- no shared state, no risk to
    a real ~/.local/share/muxplex/sessions.json anywhere on this host."""
    import muxplex.manifest as manifest_mod

    monkeypatch.setattr(manifest_mod, "MANIFEST_PATH", tmp_path / "sessions.json")


# ---------------------------------------------------------------------------
# Scenario 1: muxplex restarts, tmux server survives -- must be a no-op
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_same_server_restart_is_a_noop():
    """Two consecutive poll cycles against the SAME live tmux server, with no
    session change, must leave pending_restore at None and not re-flag the
    manifest as changed on the second pass."""
    socket = unique_socket("mxp-same")
    tmux(socket, "new-session", "-d", "-s", "alpha")
    tmux(socket, "new-session", "-d", "-s", "beta")
    try:
        epoch = await probe_on_socket(socket)
        assert epoch is not None

        names = await live_names_on_socket(socket)
        manifest = load_manifest()
        manifest, changed1 = update_manifest(manifest, epoch, names)
        assert changed1 is True  # first observation: sessions newly recorded
        save_manifest(manifest)

        # Second cycle: nothing changed on the tmux side at all.
        epoch2 = await probe_on_socket(socket)
        names2 = await live_names_on_socket(socket)
        manifest2 = load_manifest()
        manifest2, changed2 = update_manifest(manifest2, epoch2, names2)

        assert changed2 is False, (
            "a same-server cycle with an unchanged session set must not be "
            "flagged as changed -- this is what keeps manifest writes near "
            "zero in steady state"
        )
        assert manifest2["pending_restore"] is None
        assert set(manifest2["sessions"]) == {"alpha", "beta"}
    finally:
        tmux(socket, "kill-server", check=False)


# ---------------------------------------------------------------------------
# Scenario 2: a session is deliberately killed while the server stays alive
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_deliberate_kill_tombstones_never_pending_restore():
    """Killing ONE session while the tmux server keeps running must remove
    it from the manifest permanently -- and it must NEVER appear in
    pending_restore. This is the sharpest failure mode the design targets:
    resurrecting a session the user deliberately closed is worse than not
    restoring at all.
    """
    socket = unique_socket("mxp-kill")
    tmux(socket, "new-session", "-d", "-s", "keep-me")
    tmux(socket, "new-session", "-d", "-s", "kill-me-on-purpose")
    try:
        epoch = await probe_on_socket(socket)
        names = await live_names_on_socket(socket)
        assert set(names) == {"keep-me", "kill-me-on-purpose"}

        manifest = load_manifest()
        manifest, _ = update_manifest(manifest, epoch, names)
        save_manifest(manifest)
        assert "kill-me-on-purpose" in manifest["sessions"]

        # The server itself is untouched -- only ONE session dies.
        tmux(socket, "kill-session", "-t", "kill-me-on-purpose")

        epoch2 = await probe_on_socket(socket)
        names2 = await live_names_on_socket(socket)
        assert names2 == ["keep-me"]

        manifest2 = load_manifest()
        manifest2, changed = update_manifest(manifest2, epoch2, names2)
        save_manifest(manifest2)

        assert changed is True
        assert "kill-me-on-purpose" not in manifest2["sessions"], (
            "a session killed against a live, identity-matched server must "
            "be tombstoned (removed), not merely marked -- so it cannot "
            "later be mistaken for something lost to a cold start"
        )
        assert manifest2["pending_restore"] is None, (
            "a deliberate kill must NEVER populate pending_restore -- "
            "muxplex must never propose resurrecting a session the user "
            "closed on purpose"
        )
        assert "keep-me" in manifest2["sessions"]

        # Reload from disk to prove this isn't just an in-memory artifact --
        # the tombstone is durable.
        reloaded = load_manifest()
        assert "kill-me-on-purpose" not in reloaded["sessions"]
        assert reloaded["pending_restore"] is None
    finally:
        tmux(socket, "kill-server", check=False)


# ---------------------------------------------------------------------------
# Scenario 3: the tmux SERVER itself dies (cold start) -- the sharpest proof
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_server_death_populates_pending_restore_and_survives_recovery():
    """The whole point of the feature: kill the entire tmux server (not a
    session), bring up a NEW server on the SAME socket name, and confirm:

      1. The old sessions land in pending_restore, tagged with the OLD
         (now-dead) server's identity.
      2. The manifest is NOT self-clearing -- unlike pruning.json, recovery
         (a new session reappearing under the SAME name) must not erase the
         pending_restore record for the OTHERS still missing.
    """
    socket = unique_socket("mxp-cold")
    tmux(socket, "new-session", "-d", "-s", "a2a")
    tmux(socket, "new-session", "-d", "-s", "bbs")
    tmux(socket, "new-session", "-d", "-s", "ccc")

    try:
        epoch_old = await probe_on_socket(socket)
        assert epoch_old is not None
        old_pid = epoch_old["server_pid"]

        names = await live_names_on_socket(socket)
        assert set(names) == {"a2a", "bbs", "ccc"}

        manifest = load_manifest()
        manifest, _ = update_manifest(manifest, epoch_old, names)
        save_manifest(manifest)

        # ---- The tmux SERVER itself dies (socket-scoped kill-server, not
        #      a session kill -- this simulates a host reboot / OOM / the
        #      cgroup-wide SIGKILL from the 2026-07-29 incident). ----
        tmux(socket, "kill-server", check=False)

        # Confirm the server is really gone before proceeding.
        gone_epoch = await probe_on_socket(socket)
        assert gone_epoch is None, "tmux server must be fully down at this point"

        # ---- A NEW server comes up on the SAME socket name (same
        #      TMUX_TMPDIR-equivalent path a real restart would reuse),
        #      with none of the old sessions -- the cold-start case. ----
        tmux(socket, "new-session", "-d", "-s", "fresh-shell-only")

        epoch_new = await probe_on_socket(socket)
        assert epoch_new is not None
        assert epoch_new["server_pid"] != old_pid, (
            "the new tmux server must have a different pid from the dead one"
        )

        names_new = await live_names_on_socket(socket)
        assert names_new == ["fresh-shell-only"]

        manifest2 = load_manifest()
        manifest2, changed = update_manifest(manifest2, epoch_new, names_new)
        save_manifest(manifest2)

        assert changed is True
        pending = manifest2["pending_restore"]
        assert pending is not None, (
            "an unplanned tmux server death must populate pending_restore "
            "-- this is the entire fix for the 2026-07-29 incident, where "
            "44 session names were lost because nothing recorded them"
        )
        assert pending["lost_epoch"]["server_pid"] == old_pid
        assert set(pending["sessions"]) == {"a2a", "bbs", "ccc"}

        # ---- Recovery in progress: "a2a" comes back (e.g. a human ran
        #      amplifier-workspace for it manually). The OTHER two names
        #      (bbs, ccc) must NOT be dropped from pending_restore just
        #      because ONE of their siblings reappeared -- this is exactly
        #      the bug pruning.json has (an entry vanishes the moment the
        #      session it tracks reappears), which is what destroyed the
        #      recovery list mid-recovery during the real incident. ----
        tmux(socket, "new-session", "-d", "-s", "a2a")
        epoch_recovering = await probe_on_socket(socket)
        names_recovering = await live_names_on_socket(socket)
        assert set(names_recovering) == {"fresh-shell-only", "a2a"}

        manifest3 = load_manifest()
        manifest3, _ = update_manifest(manifest3, epoch_recovering, names_recovering)
        save_manifest(manifest3)

        # Same epoch as epoch_new (server didn't change again) -> same-server
        # branch runs. pending_restore is untouched by that branch entirely.
        assert manifest3["pending_restore"] is not None
        assert set(manifest3["pending_restore"]["sessions"]) == {"a2a", "bbs", "ccc"}, (
            "pending_restore must survive a partial, in-progress recovery -- "
            "it is a frozen snapshot from the moment of detection, not a "
            "live view that erodes as individual names come back"
        )

        # And the manifest's live `sessions` view correctly reflects reality:
        # "a2a" is live again under the NEW epoch.
        assert "a2a" in manifest3["sessions"]

        # Final sanity: reload from disk -- the whole point is durability
        # across process boundaries, not just in-memory correctness.
        reloaded = load_manifest()
        assert reloaded["pending_restore"] is not None
        assert set(reloaded["pending_restore"]["sessions"]) == {"a2a", "bbs", "ccc"}
    finally:
        tmux(socket, "kill-server", check=False)


# ---------------------------------------------------------------------------
# Scenario 4: no server at all -- knowledge unavailable, never a cold start
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_no_server_running_never_declares_cold_start():
    """probe_tmux_epoch() against a socket with NO server at all returns
    None, and update_manifest() must leave any existing manifest completely
    untouched -- absence of a server is not evidence the recorded sessions
    are gone, and must never populate pending_restore on its own."""
    socket = unique_socket("mxp-absent")
    # Deliberately never start a server on this socket name.

    epoch = await probe_on_socket(socket)
    assert epoch is None

    manifest = {
        "schema": 1,
        "epoch": {"socket_path": "/irrelevant", "server_pid": 1, "inode": 1},
        "sessions": {"whatever": {"first_seen_at": 1.0, "last_seen_at": 2.0}},
        "pending_restore": None,
    }
    new_manifest, changed = update_manifest(manifest, epoch, [])

    assert changed is False
    assert new_manifest["pending_restore"] is None
    assert "whatever" in new_manifest["sessions"]

"""Integration tests for scrollback paging against a REAL, isolated tmux
server (docs/plans/2026-08-07-scrollback-paging-plan.md §6, evidence items
1-10 except item 10 -- Phase 0 retention truth, already covered by
test_sessions.py's test_muxplex_never_sets_history_limit()).

Run with:
    pytest -m integration -v muxplex/tests/test_scrollback_paging_integration.py

These tests spin up their OWN isolated tmux server on a unique `-L <socket>`
name, layered on top of conftest.py's autouse `TMUX_TMPDIR` isolation (see
AGENTS.md "Any test or proof that arms this hook for real must run against
an isolated tmux server -- never the ambient one"). They never touch the
default socket, the ambient `$TMUX_TMPDIR`, or port 8088.
"""

from __future__ import annotations

import asyncio
import subprocess
import uuid

import pytest

from muxplex.sessions import capture_pane_metadata, capture_pane_window

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Isolated tmux server fixture -- same pattern as test_integration.py's
# `tmux_server`, but module-scoped here with its own unique socket name so
# concurrent test modules (or `make test` runs) never collide.
# ---------------------------------------------------------------------------


def _tmux(socket: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["tmux", "-L", socket, *args],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def paging_socket():
    """A fresh, isolated tmux server + one wide/tall session ('paging'),
    torn down after the test. Each test gets its OWN socket (function
    scope, not module scope) so history state never leaks between tests.
    """
    socket = f"paging-test-{uuid.uuid4().hex[:12]}"
    result = _tmux(socket, "new-session", "-d", "-s", "paging", "-x", "200", "-y", "40")
    assert result.returncode == 0, f"failed to start isolated tmux: {result.stderr}"
    yield socket
    _tmux(socket, "kill-server")


def make_run_tmux_for_socket(socket: str):
    """Return an async run_tmux substitute routing every call through the
    isolated *socket* -- same helper as test_integration.py's, duplicated
    here rather than imported (test modules are independent; see AGENTS.md
    on duplicated-for-different-failure-model callers)."""

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

    return patched_run_tmux


async def _emit(
    socket: str, name: str, shell_cmd: str, settle: float = 0.6, timeout: float = 30.0
) -> None:
    """Run *shell_cmd* in the target pane, then block until it has
    GENUINELY finished AND tmux's own history bookkeeping has caught up.

    A fixed-sleep proxy for "is the shell loop done" is flaky under a
    slower/CPU-constrained container (measured: a 5000-line loop given
    only settle=3.0s left history_size short of 5000 on this DTU's arm64
    host). Waiting for a completion marker to become VISIBLE is not
    sufficient either -- measured directly: `history_size` read 0
    immediately after a 2000-line burst's marker became visible, then 186
    a full second later. tmux's redraw/history bookkeeping lags behind
    the raw bytes landing in the pty under a large burst. So this waits
    for the marker, THEN polls `history_size` until it stops changing
    across two consecutive samples -- the only reliable "tmux has fully
    caught up" signal. *settle* is a floor wait before the first poll,
    not the whole budget.
    """
    marker = f"MUXPLEX-TEST-DONE-{uuid.uuid4().hex[:10]}"
    _tmux(socket, "send-keys", "-t", name, f"{shell_cmd}; echo {marker}", "Enter")
    await asyncio.sleep(settle)
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        result = _tmux(socket, "capture-pane", "-p", "-t", name, "-S", "-5")
        if marker in result.stdout:
            break
        await asyncio.sleep(0.3)
    else:
        raise AssertionError(
            f"timed out after {timeout}s waiting for completion marker {marker!r} "
            f"in pane {name!r} -- the emitted command never finished"
        )

    prev_h: int | None = None
    while loop.time() < deadline:
        h, _p, _limit = await capture_pane_metadata(name)
        if h == prev_h:
            return
        prev_h = h
        await asyncio.sleep(0.3)
    raise AssertionError(
        f"timed out after {timeout}s waiting for history_size to stabilize "
        f"in pane {name!r} (last seen: {prev_h})"
    )


async def _set_history_limit_before_window(socket: str, session: str, limit: int):
    """history-limit binds a pane at CREATION time, not afterward (plan §1
    -- already fixed in this repo via ensure_history_retention()'s
    removal). To get a real, small history-limit for the saturation test,
    set the option THEN create a fresh window that inherits it -- the
    documented, measured way to make it bind (plan §1.2)."""
    _tmux(socket, "set-option", "-t", session, "history-limit", str(limit))
    _tmux(socket, "new-window", "-t", session, "-n", "bound")
    await asyncio.sleep(0.2)
    return f"{session}:bound"


# ---------------------------------------------------------------------------
# Evidence item 1: drift regression -- the reason this design exists
# (plan §2.2/§2.3, §6 item 1)
# ---------------------------------------------------------------------------


async def test_drift_regression_same_before_is_byte_identical(
    paging_socket, monkeypatch
):
    """Reading the SAME absolute `before` range before and after new output
    arrives must return byte-identical content -- and a NAIVE relative
    request re-using the same raw tmux `-S`/`-E` coordinates across that
    gap must NOT (that's the bug this feature exists to fix)."""
    monkeypatch.setattr(
        "muxplex.sessions.run_tmux", make_run_tmux_for_socket(paging_socket)
    )
    name = "paging"
    await _emit(paging_socket, name, "for i in $(seq 1 400); do echo LINE-$i; done")

    h1, _p1, _limit1 = await capture_pane_metadata(name)
    # Page: the 10 rows immediately older than absolute position (h1 - 90).
    before = h1 - 90
    row_count = 10
    start_guess = before - row_count
    rel_s1 = start_guess - h1
    rel_e1 = start_guess + row_count - 1 - h1
    _, _, _, first_read = await capture_pane_window(name, rel_s1, rel_e1)

    # New output scrolls in -- history_size grows.
    await _emit(paging_socket, name, "for i in $(seq 9000 9030); do echo NEW-$i; done")
    h2, _, _ = await capture_pane_metadata(name)
    assert h2 > h1, "test setup requires history_size to have grown"

    # Correct (abs-anchored) re-request of the SAME absolute range: convert
    # using the FRESH h2, not the stale rel1 coordinates.
    rel_s2 = start_guess - h2
    rel_e2 = start_guess + row_count - 1 - h2
    _, _, _, second_read_correct = await capture_pane_window(name, rel_s2, rel_e2)
    assert second_read_correct == first_read, (
        "the SAME absolute range must be byte-identical across requests"
    )

    # The bug this replaces: re-using the ORIGINAL relative coordinates
    # verbatim after new output arrived returns DIFFERENT text.
    _, _, _, naive_relative_reread = await capture_pane_window(name, rel_s1, rel_e1)
    assert naive_relative_reread != first_read, (
        "naive relative-coordinate reuse should drift once new output "
        "has scrolled in -- if this assertion fails, the drift bug this "
        "feature exists to fix may no longer reproduce on this tmux "
        "version, which would need re-investigation, not deletion"
    )


# ---------------------------------------------------------------------------
# Evidence item 2: round-trip completeness (plan §6 item 2)
# ---------------------------------------------------------------------------


async def test_round_trip_paging_is_complete_no_gaps_no_duplicates(
    paging_socket, monkeypatch
):
    """Paging backward at lines=500 until has_more:false, then
    concatenating, must reproduce every one of 5000 uniquely-numbered
    emitted lines exactly once -- no gaps, no duplicates."""
    monkeypatch.setattr(
        "muxplex.sessions.run_tmux", make_run_tmux_for_socket(paging_socket)
    )
    name = "paging"
    _tmux(paging_socket, "set-option", "-t", name, "history-limit", "20000")
    _tmux(paging_socket, "new-window", "-t", name, "-n", "deep")
    target = f"{name}:deep"
    await asyncio.sleep(0.2)

    await _emit(
        paging_socket,
        target,
        "for i in $(seq 1 5000); do echo ROW-$i; done",
        settle=3.0,
    )

    h, p, _limit = await capture_pane_metadata(target)
    # The most recent ~pane_height rows are still on the VISIBLE screen,
    # not yet counted in history_size -- total addressable range (h + p)
    # is the correct sanity check here, not h alone (measured: h alone
    # undercounts by roughly pane_height for a burst this size).
    assert h + p >= 5000, (
        f"expected total addressable rows >= 5000, got h={h} p={p} "
        "(history-limit too low?)"
    )

    pages: list[str] = []
    before: int | None = None
    lines = 500
    guard = 0
    while True:
        guard += 1
        assert guard < 50, "paging loop did not terminate -- has_more never False"
        if before is None:
            h_i, _p_i, _l_i, text = await capture_pane_window(target, -lines, None)
            start = max(0, h_i - lines)
        else:
            row_count = min(lines, before)
            start_guess = before - row_count
            if row_count == 0:
                break
            rel_s = start_guess - h
            rel_e = start_guess + row_count - 1 - h
            h_i, _p_i, _l_i, text = await capture_pane_window(target, rel_s, rel_e)
            start = h_i + rel_s
        pages.append(text)
        has_more = start > 0
        if not has_more:
            break
        before = start

    full_text = "".join(reversed(pages))
    rows = [int(m) for m in _extract_row_numbers(full_text)]
    assert rows == sorted(rows), "rows must appear in increasing order (no reordering)"
    assert len(rows) == len(set(rows)), "no duplicate rows across pages"
    assert set(rows) == set(range(1, 5001)), "every emitted row 1..5000 must be present"


def _extract_row_numbers(text: str) -> list[str]:
    import re

    return re.findall(r"ROW-(\d+)", text)


# ---------------------------------------------------------------------------
# Evidence item 3: true beginning vs. retention wall (plan §6 item 3)
# ---------------------------------------------------------------------------


async def test_saturated_pane_reports_retention_wall(paging_socket, monkeypatch):
    """A SATURATED pane (history-limit small enough to have evicted) must
    report has_more:false, saturated:true at its terminal page -- NOT the
    same has_more:false, saturated:false an unsaturated pane's true
    beginning reports."""
    monkeypatch.setattr(
        "muxplex.sessions.run_tmux", make_run_tmux_for_socket(paging_socket)
    )
    target = await _set_history_limit_before_window(paging_socket, "paging", 200)
    await _emit(
        paging_socket,
        target,
        "for i in $(seq 1 2000); do echo SAT-$i; done",
        settle=2.0,
    )

    # tmux does not trim history down to EXACTLY history_limit on every
    # single line -- measured directly: after a large burst settles,
    # history_size can rest a little BELOW history_limit (e.g. 186 for a
    # limit of 200), then grow past it again as each further line arrives,
    # before the next internal trim. So "saturated" is a real, momentarily
    # true condition on an actively-growing pane, not a permanent plateau
    # at the exact limit. Nudge it there deterministically: keep adding
    # single lines until a fresh read reports h >= limit.
    h, _p, limit = await capture_pane_metadata(target)
    guard = 0
    while h < limit:
        guard += 1
        assert guard < 100, (
            f"history_size ({h}) never reached history_limit ({limit}) "
            "after repeated nudges -- real tmux eviction behavior may "
            "have changed and needs re-investigation"
        )
        await _emit(paging_socket, target, f"echo NUDGE-{guard}", settle=0.1)
        h, _p, limit = await capture_pane_metadata(target)
    assert h >= limit, "test setup requires the pane to have saturated"

    # Independent, model-free proof that real eviction occurred: the
    # earliest emitted line must no longer be retrievable at all.
    _, _, _, oldest = await capture_pane_window(target, -(h + 5), None)
    assert "SAT-1\n" not in oldest and "SAT-1-" not in oldest, (
        "SAT-1 should have been evicted by history-limit=200 after 2000+ lines"
    )

    # Page all the way back to the terminal page.
    before: int | None = None
    lines = 50
    guard = 0
    last_has_more = True
    last_saturated = False
    while last_has_more:
        guard += 1
        assert guard < 50
        if before is None:
            h_i, _p_i, l_i, _ = await capture_pane_window(target, -lines, None)
            start = max(0, h_i - lines)
        else:
            row_count = min(lines, before)
            start_guess = before - row_count
            if row_count == 0:
                start = start_guess
                h_i, l_i = h, limit
                last_has_more = False
                last_saturated = h_i >= l_i
                break
            rel_s = start_guess - h
            rel_e = start_guess + row_count - 1 - h
            h_i, _p_i, l_i, _ = await capture_pane_window(target, rel_s, rel_e)
            start = h_i + rel_s
        last_has_more = start > 0
        last_saturated = h_i >= l_i
        before = start

    assert last_has_more is False
    assert last_saturated is True


async def test_unsaturated_pane_reports_true_beginning(paging_socket, monkeypatch):
    """The unsaturated counterpart: a pane whose history-limit was never
    reached must report has_more:false, saturated:false at the true
    beginning -- proving the two states are genuinely distinguished, not
    just always True."""
    monkeypatch.setattr(
        "muxplex.sessions.run_tmux", make_run_tmux_for_socket(paging_socket)
    )
    target = await _set_history_limit_before_window(paging_socket, "paging", 50000)
    await _emit(paging_socket, target, "for i in $(seq 1 100); do echo SMALL-$i; done")

    h, _p, limit = await capture_pane_metadata(target)
    assert h < limit, "test setup requires the pane NOT to have saturated"

    lines = 500  # deliberately larger than available history
    h_i, _p_i, l_i, _ = await capture_pane_window(target, -lines, None)
    start = max(0, h_i - lines)
    has_more = start > 0
    saturated = h_i >= l_i

    assert has_more is False
    assert saturated is False


# ---------------------------------------------------------------------------
# Evidence item 9: cost -- a deep, narrow page is cheap (plan §2.5, §6 item 9)
# ---------------------------------------------------------------------------


async def test_deep_narrow_page_is_small(paging_socket, monkeypatch):
    """A 10-row window fetched ~far into deep history must return a small
    payload (<= 2KB), proving cost tracks WINDOW size, not DEPTH -- the
    fact that makes unbounded-depth paging safe at the existing
    MAX_CAPTURE_LINES window cap."""
    monkeypatch.setattr(
        "muxplex.sessions.run_tmux", make_run_tmux_for_socket(paging_socket)
    )
    target = "paging"
    _tmux(paging_socket, "set-option", "-t", target, "history-limit", "60000")
    _tmux(paging_socket, "new-window", "-t", target, "-n", "wide")
    target = f"{target}:wide"
    await asyncio.sleep(0.2)
    await _emit(
        paging_socket,
        target,
        "for i in $(seq 1 45000); do echo DEEP-$i; done",
        settle=6.0,
    )

    h, _p, _limit = await capture_pane_metadata(target)
    assert h >= 40000, f"expected deep history, got history_size={h}"

    before = h - 40000
    lines = 10
    row_count = min(lines, before)
    start_guess = before - row_count
    rel_s = start_guess - h
    rel_e = start_guess + row_count - 1 - h
    _, _, _, text = await capture_pane_window(target, rel_s, rel_e)

    assert len(text.encode("utf-8")) <= 2048, (
        f"deep narrow page returned {len(text.encode('utf-8'))} bytes, expected <= 2048"
    )


# ---------------------------------------------------------------------------
# Silent-clamp-fails-loud proof: raw tmux clamps out-of-range silently
# (exit 0); the server-level `before` bound check must turn that into a
# loud 400 instead (plan §2.4, §3.2).
# ---------------------------------------------------------------------------


def test_raw_tmux_clamps_out_of_range_silently(paging_socket):
    """Baseline proof of the underlying tmux behavior the server-level
    `before` bounds check exists to hide from callers: an out-of-range
    `-S` exits 0 with a silently clamped (not an error) result."""
    result = _tmux(paging_socket, "capture-pane", "-p", "-t", "paging", "-S", "-999999")
    assert result.returncode == 0, (
        "tmux capture-pane must clamp out-of-range silently (exit 0), "
        "which is exactly why the SERVER must validate `before` itself "
        "rather than trusting tmux's own error signaling"
    )

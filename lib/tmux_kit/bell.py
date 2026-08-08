"""Bell *detection* -- the tmux facts, none of muxplex's attention model.

Moved from ``bells.py`` (``poll_bell_flag``) and ``main.py`` (the
``run-shell`` hook-string construction) at tmux-lib extraction stage S1
(plan §7.1, §3.2 -- docs/plans/2026-08-08-tmux-lib-extraction-plan.md).

This module contains the ONLY ``run-shell`` construction site in the
production source tree. ``test_safety_rails.py``'s AST rail enforces that,
recursively, and additionally enforces ZERO sites in app-level modules --
the §3.2 two-rail tightening. See AGENTS.md's "never render to a pane"
rule: tmux's ``run-shell`` paints a background command's output onto a live
client's active pane, which has caused real, repeated production incidents.

What stays app-side (plan §3.2): the hook's *content* (muxplex's curl
command, scheme, port -- ``main.py``'s ``_bell_hook_curl()``), the
``set-hook`` arming/retry policy, and the entire attention model
(``unseen_count`` / ``seen_at`` / clear rules -- ``bells.py``).
"""

from __future__ import annotations

from tmux_kit.proc import run_tmux


async def poll_bell_flag(session_name: str) -> bool:
    """Poll ALL windows of session_name's tmux window_bell_flag.

    Calls: tmux list-windows -t <name> -F #{window_bell_flag}

    Returns True if ANY window in the session reports '1', False otherwise
    (including on errors, or zero windows).

    Incident (verified against real tmux): this used to call
    ``display-message -t <name> -p '#{window_bell_flag}'``, whose session-only
    target resolves to the session's CURRENT (active) window -- not
    necessarily the window a bell fired in. Confirmed live: firing a bell in
    an inactive window set THAT window's flag to '1' while the active
    window's flag stayed '0', and ``display-message -t <session>`` (no window
    qualifier) reported the active window's ('0') flag -- the bell was
    invisible to this fallback despite the tmux-native flag being correctly
    set. A multi-window session (e.g. an amplifier-workspace-style layout)
    whose bell fires in a background window went undetected by this path.
    ``list-windows -t <session>`` enumerates every window in the session, so
    a bell in ANY of them is now seen regardless of which window is active.

    Note: reading does NOT clear the tmux bell flag.
    """
    try:
        output = await run_tmux(
            "list-windows", "-t", session_name, "-F", "#{window_bell_flag}"
        )
        return "1" in output.split()
    except RuntimeError:
        return False


def build_alert_bell_hook(command: str) -> str:
    """Wrap *command* in tmux's ``run-shell '<command>'`` hook form.

    This is the ONE legal ``run-shell`` construction site in the whole
    production source tree (enforced by ``test_safety_rails.py``'s AST
    rail, recursively). The caller supplies *what to run*; there is
    deliberately NO parameter that can request a loud variant -- the same
    structural property ``main.py``'s ``_bell_hook_curl()`` already has
    (plan §3.2). tmux's ``run-shell``, per its own manual, displays a
    background command's output in view mode on the client's active pane,
    so *command* itself MUST be fully silent in every failure mode (no
    stderr, no stdout, exit status 0 -- see ``_bell_hook_curl()``'s
    docstring for the three independent silences and the incident where a
    loud variant painted ``returned 52`` onto the owner's live panes).

    Byte-identical to the inline f-string this replaces
    (``f"run-shell '{...}'"`` in ``main.py``'s ``_arm_bell_hook()``) --
    S1 is a pure move; the tightening is that the construction now lives
    behind an API with no loudness knob, and app code is allowed zero
    ``run-shell`` construction sites of its own.
    """
    return f"run-shell '{command}'"

"""Completion sentinel for driving a shell command to completion over the API.

Pure, no I/O -- fully testable without a server, and directly reusable by a
caller who wants to run their own poll loop instead of `run_shell_command()`.

ASSUMES the target pane is an idle POSIX-ish shell prompt. None of this holds
for a pane running vim, a REPL, `less`, a TUI, an ssh session, or fish/nushell
(`$status`, `$env.LAST_EXIT_CODE`) unless the caller supplies a matching
`exit_expr`. See AGENT_GUIDE.md §6.2/§6.4 for the operational convention this
implements.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

# Length (in random bytes, before urlsafe-base64 encoding) of an
# auto-generated token. Short enough to keep the composed shell line
# readable, long enough that two concurrent commands in the same session
# don't collide.
_TOKEN_BYTES = 6


@dataclass(frozen=True)
class Sentinel:
    """A single-use completion marker: a token plus its digit-anchored regex."""

    token: str
    pattern: re.Pattern[str]  # MUXPLEX_DONE_<token>_EXIT_(\d+)

    def wrap(
        self, command: str, *, bell_on_failure: bool = True, exit_expr: str = "$?"
    ) -> str:
        """Compose the one-liner from AGENT_GUIDE.md §6.2/§6.4:

            <cmd>; rc=<exit_expr>; [ $rc -ne 0 ] && printf '\\a'; \\
                echo "MUXPLEX_DONE_<token>_EXIT_$rc"

        With bell_on_failure=False, the `printf '\\a'` clause is omitted
        entirely (never fire the bell for a routine, expected-to-fail
        check, per AGENT_GUIDE.md §6.4's recommended convention: ring on
        nonzero exit only, never on routine success).
        """
        parts = [command, f"rc={exit_expr}"]
        if bell_on_failure:
            parts.append("[ $rc -ne 0 ] && printf '\\a'")
        parts.append(f'echo "MUXPLEX_DONE_{self.token}_EXIT_$rc"')
        return "; ".join(parts)

    def search(self, snapshot: str) -> int | None:
        """Digit-anchored match. Returns the real exit code, or None.

        The digit anchor is load-bearing: tmux echoes your input line into
        the pane BEFORE the shell runs it, so the literal, unexpanded
        "...EXIT_$?" text is visible in the pane immediately -- before the
        command has even started, let alone finished. A bare-token
        substring check (`"MUXPLEX_DONE_<token>" in snapshot`) false-matches
        on that echo and reports "done" with a bogus exit code instantly.
        Shell expansion never happens inside that echoed input line, so
        `\\d+` only ever matches the version the shell actually wrote back
        out after running the command to completion.

        Do NOT "simplify" this to a bare-token check -- see
        `tests/test_sentinel.py::test_digit_anchor_regression` for the
        regression this guards against.
        """
        match = self.pattern.search(snapshot)
        if match is None:
            return None
        return int(match.group(1))


def make_sentinel(token: str | None = None) -> Sentinel:
    """Create a new Sentinel. `token` defaults to a short random urlsafe string."""
    if token is None:
        token = secrets.token_urlsafe(_TOKEN_BYTES)
    pattern = re.compile(rf"MUXPLEX_DONE_{re.escape(token)}_EXIT_(\d+)")
    return Sentinel(token=token, pattern=pattern)

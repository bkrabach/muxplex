"""tmux session-name validation (security boundary) and exact-match rename.

Moved verbatim from ``sessions.py`` (tmux-lib extraction stage S1, plan
§7.1). These are tmux's own facts -- its target-separator charset, its
silent ``.``->``_`` mangling, its exact-match ``=target`` form -- not
muxplex facts, which is why they are library (plan §2).
"""

from __future__ import annotations

import re

from tmuxkit.proc import run_tmux

# ---------------------------------------------------------------------------
# Session-name validation (security boundary)
# ---------------------------------------------------------------------------

# Canonical allowlist for client-supplied session names. A name that matches
# this pattern contains no shell metacharacters, whitespace, or the tmux target
# separator (`:`), so it is safe to substitute into a shell template
# (create/delete session commands) and safe as a `tmux -t` target.
#
# This is the PRIMARY defense against shell injection via session names. Every
# API endpoint that accepts a client-supplied session name and forwards it to a
# subprocess (create, delete, connect, and any future input endpoint) MUST run
# the name through `is_valid_session_name()` at the boundary, BEFORE any
# substitution or subprocess call.
#
# Charset rationale: tmux forbids `:` in session names (it's the
# session:window.pane target separator), so excluding it costs nothing. All 68
# of the deployment's live session names pass this pattern; it does not reject
# any legitimate existing name.
# The first character MUST be alphanumeric or underscore. This is deliberate and
# security-load-bearing: a leading ``-`` would let a valid name be parsed as an
# OPTION by tmux or by a user-configurable template command (argument injection),
# and ``shlex.quote`` does NOT neutralize that -- quoting stops shell-metacharacter
# interpretation, but a quoted ``-C`` or ``--destroy`` is still a flag to the
# invoked program. Forbidding a leading ``-`` (and leading ``.``/``..`` path
# traversal) closes that class. ``\A...\Z`` (not ``^...$``) is required because
# ``$`` also matches just before a trailing newline, so ``"name\n"`` would slip
# through ``^...$``. All 68 live session names pass this pattern.
SESSION_NAME_RE = re.compile(r"\A[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}\Z")


def is_valid_session_name(name: str) -> bool:
    """Return True if *name* is a safe session name per ``SESSION_NAME_RE``.

    Safe means: 1-64 chars drawn only from ASCII letters, digits, and the
    ``_ . -`` set, with an alphanumeric-or-underscore FIRST character -- no
    whitespace (including a trailing newline), no shell metacharacters, no
    ``:``, and no leading ``-`` (argument injection) or leading ``.``/``..``
    (path traversal). Callers at the API boundary reject names that fail this
    check with HTTP 400 before the name reaches any subprocess.
    """
    return bool(SESSION_NAME_RE.match(name))


def is_tmux_stable_name(name: str) -> bool:
    """Return True if tmux would create/rename a session to EXACTLY *name*,
    with no silent character mangling.

    ``SESSION_NAME_RE`` (``is_valid_session_name``) permits ``.`` in a
    session name, but tmux 3.4 silently converts ``.`` to ``_`` at
    creation/rename time -- verified empirically against a real, isolated
    tmux server (see docs/plans/2026-08-07-session-rename-plan.md \\u00a71): a
    session named via ``build.js`` actually comes out as ``build_js``, with
    exit code 0 and no error. That gap is the entire mangling problem: a
    caller requesting ``build.js`` cannot tell from the response alone that
    it got ``build_js`` instead.

    This predicate lets a caller REJECT such a request outright rather than
    predict tmux's mangling rule. Rejecting is deliberately preferred over
    modeling the substitution (``requested.replace(".", "_")``): a wrong
    prediction produces a wrong collision check and a silently mis-keyed
    session, whereas over-rejecting on a hypothetical tmux that would not
    mangle the name costs the caller one retry.

    Requires *name* to ALSO pass ``is_valid_session_name`` first -- this is
    an ADDITIONAL, stricter check for names that must survive tmux
    unchanged (currently: ``POST /api/sessions/{name}/rename``'s
    ``new_name``), not a replacement for the charset boundary every
    session-name-accepting endpoint already enforces via
    ``is_valid_session_name``. Deliberately NOT applied to the create path
    (``POST /api/sessions``) -- that is a separate, pre-existing, and
    breaking fix left for the owner to decide on its own (see the rename
    plan \\u00a73/\\u00a715).
    """
    return is_valid_session_name(name) and "." not in name


async def rename_tmux_session(old_name: str, new_name: str) -> None:
    """Run ``tmux rename-session -t =<old_name> -- <new_name>`` (argv, no
    shell).

    Raises RuntimeError (via ``run_tmux`` -- tmux's own stderr, e.g.
    ``duplicate session: <new_name>``) if tmux refuses, notably rc=1 when
    *new_name* is already a live session.

    Uses ``=<old_name>`` -- tmux's EXACT-match target form, verified live
    to work for ``rename-session`` (unlike a ``send-keys`` pane target;
    see ``terminal_input.session_target``'s docstring) -- plus ``--``
    end-of-options, giving this call a STRONGER targeting guarantee than
    ``/input`` achieves: tmux resolves an exact-match target before any
    prefix matching, so this cannot land on a differently-named neighbour.
    This is the first session-lifecycle subprocess with no shell path at
    all -- callers still validate both names first
    (``is_valid_session_name`` / ``is_tmux_stable_name``), same discipline
    as every other tmux-touching endpoint.

    tmux reports rc=0 even when it silently mangles the resulting name
    (see ``is_tmux_stable_name``'s docstring) -- callers MUST re-enumerate
    and verify the observed name after this call succeeds; this function
    only reports whether tmux accepted the request, never what the
    session ended up named.
    """
    await run_tmux("rename-session", "-t", f"={old_name}", "--", new_name)

"""
Terminal input helpers for POST /api/sessions/{name}/input.

This module is the argv-construction layer for typing into tmux sessions
over the API. It is deliberately tiny and pure (no I/O) so the security
properties are auditable at a glance and unit-testable without tmux.

Security model (see the endpoint in main.py for the enforcement order):

- Text is sent with ``tmux send-keys -l`` (literal mode) via
  ``asyncio.create_subprocess_exec`` (argv, NEVER a shell). Literal mode
  means arbitrary text -- including shell metacharacters -- is typed into
  the pane as characters, never interpreted by tmux or by any shell that
  muxplex spawns. Whatever the *pane* does with typed characters (e.g. a
  shell executing a line when Enter arrives) is the pane's own behavior,
  identical to a human typing -- that is the endpoint's purpose.
- ``--`` terminates tmux option parsing so text beginning with ``-`` cannot
  be parsed as a flag (argument-injection guard).
- Named special keys are restricted to ``ALLOWED_KEYS`` -- an explicit,
  closed set. Anything else is rejected at the API boundary (400).
- Targets are the plain session name (same as capture_pane / connect /
  delete). tmux's ``=name`` exact-match prefix is NOT valid for a
  ``send-keys`` pane target (it raises "can't find pane"), so we rely on the
  same guarantee those endpoints do: the endpoint only proceeds after an
  exact ``name in known_sessions`` membership check, and tmux resolves an
  exact session name to itself before any prefix match -- so ``-t name``
  cannot land on a neighbouring session.
- The per-session allowlist (``settings.input_allowed_sessions``) is matched
  as **glob patterns** (see ``session_matches_allowlist``), not exact
  strings -- ``"*"`` allows every session, ``"amplifier-*"`` allows a
  prefix family. A literal name with no glob metacharacters still matches
  only itself, so existing exact-name configs behave unchanged.
"""

import fnmatch

# Closed allowlist of named special keys an agent may send. These are tmux
# key names (see tmux(1) "KEY BINDINGS"). Kept deliberately small: enough to
# drive an interactive program (submit, cancel, navigate) without opening
# the full tmux key-name namespace. Extend only with explicit review.
ALLOWED_KEYS: frozenset[str] = frozenset(
    {
        "Enter",
        "Escape",
        "Tab",
        "C-c",
        "C-d",
        "Up",
        "Down",
        "Left",
        "Right",
        "PageUp",
        "PageDown",
    }
)

# Number of characters of the input text included in the info-level audit
# log line. Short by design: enough to correlate an action with its effect,
# not enough to routinely leak a secret typed through the endpoint.
PREVIEW_CHARS = 16

# Size/quantity caps on one input action. Generous for the intended use
# (an agent typing commands/answers into a pane) while bounding abuse and
# avoiding platform failure modes:
# - MAX_TEXT_BYTES: a single argv element beyond ~128 KiB raises OSError
#   (E2BIG) from exec; 8 KiB is plenty for typed input and keeps every
#   send well inside that limit. Measured in UTF-8 encoded bytes.
# - MAX_KEYS: each named key forks one tmux subprocess, so an unbounded
#   list is a fork amplifier. 64 keys is far beyond any legitimate
#   interactive sequence.
MAX_TEXT_BYTES = 8192
MAX_KEYS = 64


def session_target(name: str) -> str:
    """Return the tmux ``send-keys`` target for session *name* (the plain name).

    tmux's ``=name`` exact-match prefix is not accepted as a ``send-keys``
    pane target (it errors "can't find pane"), so we use the plain name --
    identical to ``capture_pane`` / connect / delete elsewhere in muxplex.
    The endpoint only calls this after confirming ``name`` is an exact member
    of the known-session set, and tmux resolves an exact session name to
    itself before any prefix match, so ``-t name`` cannot mis-target a
    neighbouring session. *name* has already passed ``is_valid_session_name``
    (no ``:``), so it is always a session-only target.
    """
    return name


def input_allowed_for_session(name: str, settings: dict) -> bool:
    """Return True if *settings* permits typing into session *name*.

    This is the SINGLE fence evaluation both ``POST
    /api/sessions/{name}/input`` (main.py's ``send_session_input``) and the
    terminal WS input gate (main.py's ``terminal_ws_proxy``, guarding
    ``client_to_ttyd`` for Bearer-only-authenticated callers -- see
    ``docs/API_SEMANTICS.md``'s "terminal WS input fence" section) evaluate.
    Factored out so there is exactly one place that can tighten or loosen
    either check -- two independent copies of "is this session typeable"
    is exactly the kind of drift that would let one fence quietly diverge
    from the other.

    Same fail-closed semantics as the inline check this replaced:
    - ``input_enabled`` must be the literal boolean ``True`` (a truthy
      string like ``"false"`` from a hand-edited settings.json must not
      enable the fence).
    - ``input_allowed_sessions`` must be a list; a non-list value (e.g. a
      string, which would substring-match via ``in``) is treated as empty.
    - The actual name/pattern matching is ``session_matches_allowlist``.
    """
    if settings.get("input_enabled") is not True:
        return False
    allowed = settings.get("input_allowed_sessions")
    if not isinstance(allowed, list):
        allowed = []
    return session_matches_allowlist(name, allowed)


def session_matches_allowlist(name: str, patterns: list) -> bool:
    """Return True if *name* matches at least one glob pattern in *patterns*.

    This is the entire security boundary for who a remote agent may type
    into, so its matching rules are deliberate and non-negotiable:

    - Matching is **case-INSENSITIVE** (operator preference), but achieved
      deterministically: both *name* and *pattern* are explicitly
      ``.casefold()``-ed, then compared with ``fnmatch.fnmatchcase``. Do
      not "simplify" this to plain ``fnmatch.fnmatch`` for the
      case-insensitivity -- ``fnmatch.fnmatch`` gets its case-folding as a
      side effect of ``os.path.normcase``, which is a no-op on Linux and
      case-folding on macOS/Windows. That makes ``fnmatch.fnmatch``'s
      behavior *platform-dependent* (case-sensitive on Linux, insensitive
      on macOS/Windows) -- exactly the kind of environment-dependent
      security fence that must never exist. Explicit ``casefold()`` +
      ``fnmatchcase`` gives the same, deliberately case-insensitive result
      on every platform muxplex runs on. (``.casefold()`` rather than
      ``.lower()``: it's the correct Unicode-aware case-normalization;
      session names are ASCII-restricted by ``is_valid_session_name`` so
      the two coincide here, but casefold is the right habit.)
    - Empty *patterns* returns False for every *name* (fail-closed): an
      empty allowlist must deny everything, never be silently treated as
      "no restriction" / allow-all.
    - Non-string entries are skipped rather than raising, so a malformed
      settings.json (e.g. a stray int or null in the list) degrades to
      "that one entry never matches" instead of a 500 on every input call.
    - A literal pattern with no glob metacharacters (``*``, ``?``, ``[...]``)
      matches only that exact name (case-insensitively), so pre-existing
      exact-name configs keep working, just no longer case-sensitive.

    Patterns are operator-supplied from a local settings.json file (see
    ``settings.LOCAL_ONLY_KEYS`` -- never PATCHable, never federation-synced),
    not untrusted network input, so fnmatch's glob-to-regex translation is
    not a ReDoS surface here. *name* has already passed
    ``is_valid_session_name`` (charset restricted to
    ``[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}``) before this function ever runs, so
    there are no path-separator or traversal edge cases for the glob to
    interact with.
    """
    folded_name = name.casefold()
    for pattern in patterns:
        if not isinstance(pattern, str):
            continue
        if fnmatch.fnmatchcase(folded_name, pattern.casefold()):
            return True
    return False


def build_send_text_argv(name: str, text: str) -> list[str]:
    """Build the argv for literally typing *text* into session *name*.

    ``-l`` = literal (no key-name lookup, no expansion); ``--`` = end of
    options (text starting with ``-`` stays data). The returned argv is for
    ``create_subprocess_exec`` -- it must never be joined into a shell string.
    """
    return ["send-keys", "-l", "-t", session_target(name), "--", text]


def build_send_key_argv(name: str, key: str) -> list[str]:
    """Build the argv for sending one named special *key* to session *name*.

    Caller must have validated *key* against ``ALLOWED_KEYS`` first; this
    function asserts that invariant rather than silently trusting it.
    """
    if key not in ALLOWED_KEYS:
        raise ValueError(f"key {key!r} is not in the allowed key set")
    return ["send-keys", "-t", session_target(name), key]


def redact_preview(text: str, limit: int = PREVIEW_CHARS) -> str:
    """Return a short, single-line preview of *text* for audit logging.

    Truncates to *limit* characters and replaces newlines so one input
    action is always one log line.
    """
    preview = text[:limit].replace("\n", "\\n").replace("\r", "\\r")
    if len(text) > limit:
        preview += "…"
    return preview

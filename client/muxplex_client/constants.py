"""Mirrored server constants.

These are convenience mirrors of values defined authoritatively on the
muxplex server (`muxplex.sessions`, `muxplex.terminal_input`). The client
does NOT pre-validate against them -- the server is authoritative and fails
closed; a client-side copy that silently disagreed with the server would be
exactly the drift `AGENTS.md` warns about. They exist so a caller can build a
tool schema enum or a UI without a round trip.

`muxplex/tests/test_client_contract.py` (in the server's own test suite)
asserts each of these equals its server original, converting what would
otherwise be a silent drift hazard into a CI-enforced invariant.
"""

from __future__ import annotations

# Cut-against server version. Provenance, not a runtime requirement -- see
# MuxplexClient.check_server()/AsyncMuxplexClient.check_server(), which is
# opt-in and never called automatically.
MIN_SERVER_VERSION = "0.18.0"

# Mirrors muxplex.sessions.DEFAULT_CAPTURE_LINES.
DEFAULT_CAPTURE_LINES = 30

# Mirrors muxplex.sessions.MAX_CAPTURE_LINES.
MAX_CAPTURE_LINES = 2000

# Mirrors muxplex.terminal_input.ALLOWED_KEYS.
KNOWN_KEYS: frozenset[str] = frozenset(
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

# Mirrors muxplex.terminal_input.MAX_KEYS.
MAX_KEYS = 64

# HTTP read-timeout ceiling (seconds) for the three endpoints that ask the
# server to run an operator-supplied subprocess synchronously before it can
# respond: create_session's POST /api/sessions (new_session_template),
# delete_session's DELETE /api/sessions/{name} (delete_session_template,
# with input="y\n"), and connect's POST /api/sessions/{name}/connect (kills
# and restarts ttyd). AGENT_GUIDE.md section 4: a template that hasn't
# finished in 30s is not a failure -- poll instead. The client's other (fast)
# endpoints
# keep using the ordinary client-level default; this is deliberately NOT
# that default, and is NOT the same thing as create_session's own
# `timeout=` parameter, which is the unrelated poll-for-visibility ceiling
# waiting on the ~2s read cache -- do not conflate the two.
SUBPROCESS_TIMEOUT: float = 30.0

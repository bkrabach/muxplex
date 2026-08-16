"""In-process (embedded) amplifier-agent turn execution for muxplex.

Today, `POST /api/agent/chat/completions` (muxplex/main.py) can run a chat
turn two ways:

  * "sidecar"  -- proxy the request over HTTP to a separate
                  `amplifier-agent serve chat-completions` OS process
                  (the historical, still-supported path).
  * "embedded" -- call amplifier-agent's own Python library in-process,
                  no separate daemon (the new default; see
                  docs/AGENT_CHAT_EMBEDDED.md for the full write-up).

Selected by the `MUXPLEX_AGENT_MODE` environment variable (default
"embedded"; set to "sidecar" to keep using the legacy proxy). Both paths
are wire-compatible: chat.js cannot tell which one produced a given
stream.

Package layout:
  host_tool_glue.py  -- HostToolProxy / host-tool hook / HostToolYield
                         marker, ported from amplifier-agent's
                         sidecar-oriented amplifier_agent_http package so
                         the embedded path has zero import dependency on
                         it (that package is slated for deletion once the
                         sidecar itself is fully retired -- a later pass,
                         not this one).
  wire.py            -- OpenAI Chat Completions SSE chunk builders, ported
                         subset of amplifier_agent_http/_wire.py +
                         _event_translator.py, for the same reason.
  message_shape.py   -- client (OpenAI-shape) message list -> kernel
                         (amplifier) shape, dict-based port of
                         amplifier_agent_http's request-translation
                         helpers.
  runner.py          -- PreparedBundle caching + the actual per-turn
                         session construction, streaming, and host-tool
                         yield/continuation handling.
"""

from __future__ import annotations

import os

#: "embedded" (default) or "sidecar". Any value other than exactly
#: "sidecar" is treated as "embedded" -- this is a new-code default, not a
#: security fence, so failing open to the new path (rather than silently
#: falling back to the legacy one on a typo) is the right direction: a
#: typo'd env value should NOT silently reinstate a separate daemon nobody
#: asked for.
AGENT_MODE: str = os.environ.get("MUXPLEX_AGENT_MODE", "embedded").strip().lower()


def is_embedded_mode() -> bool:
    return AGENT_MODE != "sidecar"


__all__ = ["AGENT_MODE", "is_embedded_mode"]

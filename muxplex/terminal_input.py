"""Re-export shim: this module moved to ``muxplex.tmux.keys``.

Pure move at tmux-lib extraction stage S1 (plan §7.1, §3.4 --
docs/plans/2026-08-08-tmux-lib-extraction-plan.md): the argv builders,
size caps, and the allowlist-fence *mechanism* (the deliberate
``casefold()`` + ``fnmatchcase`` platform-independence argument) are tmux
and exec facts, not muxplex policy. The policy *values*
(``input_enabled`` / ``input_allowed_sessions`` / ``LOCAL_ONLY_KEYS``)
stay app-side in ``settings.py``, exactly as before.

All existing import paths keep working through this shim; new code should
import from ``muxplex.tmux.keys``.
"""

from muxplex.tmux.keys import (
    ALLOWED_KEYS,
    MAX_KEYS,
    MAX_TEXT_BYTES,
    PREVIEW_CHARS,
    build_send_key_argv,
    build_send_text_argv,
    input_allowed_for_session,
    redact_preview,
    session_matches_allowlist,
    session_target,
)

__all__ = [
    "ALLOWED_KEYS",
    "MAX_KEYS",
    "MAX_TEXT_BYTES",
    "PREVIEW_CHARS",
    "build_send_key_argv",
    "build_send_text_argv",
    "input_allowed_for_session",
    "redact_preview",
    "session_matches_allowlist",
    "session_target",
]

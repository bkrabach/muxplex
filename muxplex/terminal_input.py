"""Re-export shim: this module moved to ``tmux_kit.keys``.

Pure move at tmux-lib extraction stage S1 (plan §7.1, §3.4 --
docs/plans/2026-08-08-tmux-lib-extraction-plan.md): the argv builders,
size caps, and the allowlist-fence *mechanism* (the deliberate
``casefold()`` + ``fnmatchcase`` platform-independence argument) are tmux
and exec facts, not muxplex policy. The policy *values*
(``input_enabled`` / ``input_allowed_sessions`` / ``LOCAL_ONLY_KEYS``)
stay app-side in ``settings.py``, exactly as before.

All existing import paths keep working through this shim; new code should
import from ``tmux_kit.keys``.

``build_exit_copy_mode_argv`` is deliberately NOT re-exported here (unlike
the other builders): as of tmux-kit 0.4.0 the two send builders below chain
it in themselves, and no code left in this app package calls it directly
(the one caller, ``main.py``'s ``send_session_input``, dropped its own
explicit call for exactly that reason -- see that function's docstring).
Re-exporting a symbol nothing here uses would be dead surface a future
reader could mistake for still being on the critical path; import it from
``tmux_kit.keys`` directly if a new consumer ever genuinely needs it
standalone.
"""

from tmux_kit.keys import (
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

"""Re-export shim: this module moved to ``muxplex.tmux.cgroup``.

Pure move at tmux-lib extraction stage S1 (plan §7.1 --
docs/plans/2026-08-08-tmux-lib-extraction-plan.md): the cgroup escape is
100% general (zero muxplex imports), written after the 44-session incident,
and any tool that spawns tmux from a systemd unit has this hazard. All
existing import paths keep working through this shim; new code should
import from ``muxplex.tmux.cgroup``.
"""

from muxplex.tmux.cgroup import (
    EnvironmentMode,
    environment_mode,
    reset_probe_cache_for_tests,
    should_escape,
    wrap_exec_argv,
    wrap_shell_argv,
)

__all__ = [
    "EnvironmentMode",
    "environment_mode",
    "reset_probe_cache_for_tests",
    "should_escape",
    "wrap_exec_argv",
    "wrap_shell_argv",
]

"""The S3 non-muxplex import smoke (plan §13.2 stage 3.5, §15.1).

The point of the `lib/tmux_kit` extraction is that a SECOND application can
import the tmux library WITHOUT depending on the muxplex server package --
no fastapi, no uvicorn, no python-pam, no httpx, no `muxplex` console
script's worth of transitive weight (the exact failure
client/pyproject.toml's no-server-dep note exists to prevent, applied in
the other direction; plan §13.1).

These tests prove that property the only way it can honestly be proven:
in a FRESH interpreter (never this pytest process, whose ``sys.modules``
is already polluted by muxplex and its deps), from a neutral working
directory (so ``tmux_kit`` resolves through the INSTALLED distribution, not
through a ``lib/``-relative path accident), importing the §15.1 public
surface as it exists today, then inspecting ``sys.modules``.

If one of these fails, the seam is wrong -- the library has grown an
app/server dependency. That is a FINDING to report, not a test to relax
(see the import-purity rail in test_safety_rails.py, which catches the
same erosion at the AST level; this one catches it at runtime, transitive
dependencies included).
"""

from __future__ import annotations

import json
import subprocess
import sys

# The §15.1 public surface AS BUILT at S3. The plan's full §15.1 listing
# also names manifest I/O (load_manifest/save_manifest), the
# Sender/SendPolicy send API, a TmuxError exception type (§15.3 open
# decision #1 -- run_tmux raises RuntimeError today), a TmuxTarget config
# object, `rename_session` (spelled `rename_tmux_session` as built), and
# (second tranche, §16) the ttyd AF_UNIX lifecycle -- none of which live in
# the library yet (see lib/tmux_kit/__init__.py's "what does NOT live
# here"). Imports below are the shipped surface; extend this list as later
# stages land those pieces.
_SURFACE_PROGRAM = """
import tmux_kit
from tmux_kit.proc import run_tmux, set_env_factory, tmux_env
from tmux_kit.spawn import spawn_session
from tmux_kit.names import (
    SESSION_NAME_RE,
    is_tmux_stable_name,
    is_valid_session_name,
    rename_tmux_session,
)
from tmux_kit.observe import (
    DEFAULT_CAPTURE_LINES,
    MAX_CAPTURE_LINES,
    capture_pane,
    capture_pane_metadata,
    capture_pane_window,
    enumerate_sessions,
    probe_tmux_epoch,
    snapshot_all,
)
from tmux_kit.presence import (
    MANIFEST_SCHEMA_VERSION,
    compute_restore_plan,
    mark_restored,
    update_manifest,
)
from tmux_kit.bell import build_alert_bell_hook, poll_bell_flag
from tmux_kit.keys import ALLOWED_KEYS, MAX_KEYS, MAX_TEXT_BYTES
from tmux_kit.cgroup import should_escape, wrap_exec_argv, wrap_shell_argv

import json
import sys

print(json.dumps(sorted(sys.modules.keys())))
"""

# Module roots that must NOT be reachable from a bare library import.
# `muxplex` is the seam itself; the rest are the muxplex server's runtime
# dependency roots (pyproject.toml [project.dependencies]) that a stdlib-only
# library must never drag in.
_FORBIDDEN_ROOTS = {
    "muxplex",
    "fastapi",
    "uvicorn",
    "starlette",
    "pam",  # python-pam's import name
    "httpx",
    "aiofiles",
    "websockets",
    "itsdangerous",
    "multipart",  # python-multipart's import name
    "cryptography",
}


def _fresh_import_modules(tmp_path) -> set[str]:
    """Run the surface-import program in a fresh interpreter, neutral cwd."""
    result = subprocess.run(
        [sys.executable, "-c", _SURFACE_PROGRAM],
        capture_output=True,
        text=True,
        cwd=tmp_path,  # neither the repo root nor lib/ -- no path accidents
        timeout=60,
    )
    assert result.returncode == 0, (
        f"importing the tmux_kit public surface failed in a fresh "
        f"interpreter:\n{result.stderr}"
    )
    return set(json.loads(result.stdout))


def test_public_surface_imports_without_the_muxplex_server(tmp_path):
    """A non-muxplex consumer can import the §15.1 surface, and doing so
    does not load muxplex or any of the server's dependency roots."""
    loaded = _fresh_import_modules(tmp_path)
    offenders = sorted(m for m in loaded if m.split(".")[0] in _FORBIDDEN_ROOTS)
    assert not offenders, (
        f"importing tmux_kit's public surface dragged in server-side "
        f"modules: {offenders}. The library must stay importable by a "
        f"second app with ZERO muxplex/server weight (plan §13.1, §14.1) "
        f"-- this means the seam is wrong. Find which lib/tmux_kit module "
        f"grew the import and invert it (config/deps are INJECTED, plan "
        f"§4.3); do not weaken this list."
    )


def test_lib_distribution_declares_zero_dependencies():
    """The stdlib-only contract, checked against installed metadata (so a
    dependency added to lib/pyproject.toml -- not just a stray import --
    also turns this red)."""
    from importlib.metadata import requires

    deps = [
        r
        for r in (requires("tmux_kit") or [])
        # optional extras (e.g. the dev test extras) are not runtime deps
        if "extra ==" not in r
    ]
    assert deps == [], (
        f"tmux_kit declares runtime dependencies: {deps}. The library is "
        f"stdlib-only by contract (plan §4.4, §14.1 -- see "
        f"lib/pyproject.toml's NOTE)."
    )

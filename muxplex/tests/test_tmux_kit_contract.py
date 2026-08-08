"""Cross-repo drift tripwire for the `tmux-kit` dependency.

WHY THIS TEST LIVES HERE (in muxplex's own suite, modeled directly on
test_client_contract.py's rationale): since the tmux-kit cutover
(docs/plans/2026-08-09-tmuxkit-own-repo-and-pypi-plan.md §3.3/§5), the tmux
session-management core is no longer a workspace member of this repo -- it
is `bkrabach/tmux-kit`, installed from PyPI as a plain `tmux-kit==X.Y.Z` pin
(pyproject.toml). The two repos release independently now (§9 prices this
cost explicitly), which means nothing stops a pin bump from silently
drifting muxplex's assumptions away from what the installed package
actually provides -- a mirrored constant renamed, a signature gaining a
parameter, a presence-preservation contract quietly narrowing. This file
runs against the INSTALLED tmux-kit at the pinned version and turns that
drift red in the SAME PR that bumps the pin, not one release later after a
user hits it in production.

It runs under this suite's existing safety rails (see conftest.py) and must
never run on a host serving a live muxplex.
"""

from __future__ import annotations

import inspect
import json
import subprocess
import sys

import pytest
import tmux_kit.bell as _kit_bell
import tmux_kit.keys as _kit_keys
import tmux_kit.observe as _kit_observe
import tmux_kit.presence as _kit_presence

from muxplex.sessions import DEFAULT_CAPTURE_LINES as APP_DEFAULT_CAPTURE_LINES
from muxplex.sessions import MAX_CAPTURE_LINES as APP_MAX_CAPTURE_LINES
from muxplex.terminal_input import ALLOWED_KEYS as APP_ALLOWED_KEYS
from muxplex.terminal_input import MAX_KEYS as APP_MAX_KEYS

# ---------------------------------------------------------------------------
# Mirrored constants -- muxplex's re-export shims (sessions.py,
# terminal_input.py) must still equal what the installed tmux-kit exports
# directly. A rename or value change on either side turns this red.
# ---------------------------------------------------------------------------


def test_default_capture_lines_matches_installed_tmux_kit():
    assert APP_DEFAULT_CAPTURE_LINES == _kit_observe.DEFAULT_CAPTURE_LINES


def test_max_capture_lines_matches_installed_tmux_kit():
    assert APP_MAX_CAPTURE_LINES == _kit_observe.MAX_CAPTURE_LINES


def test_allowed_keys_matches_installed_tmux_kit():
    assert APP_ALLOWED_KEYS == _kit_keys.ALLOWED_KEYS


def test_max_keys_matches_installed_tmux_kit():
    assert APP_MAX_KEYS == _kit_keys.MAX_KEYS


# ---------------------------------------------------------------------------
# build_alert_bell_hook must never grow a loudness parameter -- the
# never-render rail (AGENTS.md, test_safety_rails.py) depends on this
# API being structurally incapable of building a loud `run-shell` command.
# Checkable from outside the library, by introspection alone.
# ---------------------------------------------------------------------------


def test_build_alert_bell_hook_has_no_loudness_parameter():
    """The one production `run-shell` construction site accepts a single
    caller-supplied command and nothing else -- no `loud`, `verbose`,
    `silent`, `quiet`, or similar flag that could construct a variant
    tmux would echo onto a live client's pane (AGENTS.md's "never render
    to a pane" rule; the incident that rule documents happened twice).
    """
    sig = inspect.signature(_kit_bell.build_alert_bell_hook)
    param_names = {name.lower() for name in sig.parameters}
    suspicious = param_names & {"loud", "verbose", "silent", "quiet", "debug"}
    assert not suspicious, (
        f"build_alert_bell_hook gained a loudness-shaped parameter: "
        f"{suspicious}. This API backs the sole legal `run-shell` "
        f"construction site (test_safety_rails.py's never-render rail) -- "
        f"it must stay structurally incapable of a loud variant."
    )
    assert len(sig.parameters) == 1, (
        f"build_alert_bell_hook's signature changed shape "
        f"({dict(sig.parameters)}) -- verify this is still the same "
        f"single-purpose, always-silent API muxplex's bell-hook arming "
        f"code (main.py) assumes."
    )


# ---------------------------------------------------------------------------
# Import purity, verified against the INSTALLED package (not a local
# source tree -- that copy of this proof now lives in tmux-kit's own
# suite). A second application's whole reason to depend on tmux-kit
# instead of muxplex is that importing it drags in nothing server-shaped.
# ---------------------------------------------------------------------------

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

_SURFACE_PROGRAM = """
import json
import sys
import tmux_kit  # noqa: F401
print(json.dumps(sorted(sys.modules.keys())))
"""


def test_importing_installed_tmux_kit_drags_in_no_server_dependencies(tmp_path):
    """Fresh interpreter, neutral cwd (see the retired
    test_lib_import_smoke.py this replaces for the local-source version of
    this same proof, now run against the PyPI-installed package instead).
    """
    result = subprocess.run(
        [sys.executable, "-c", _SURFACE_PROGRAM],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, (
        f"importing installed tmux_kit failed in a fresh interpreter:\n{result.stderr}"
    )
    loaded = set(json.loads(result.stdout))
    offenders = sorted(m for m in loaded if m.split(".")[0] in _FORBIDDEN_ROOTS)
    assert not offenders, (
        f"the installed tmux-kit dragged in server-side modules: "
        f"{offenders}. A pin bump introduced a dependency the library's "
        f"own stdlib-only contract (dependencies = [] in its "
        f"pyproject.toml) is supposed to forbid."
    )


# ---------------------------------------------------------------------------
# Presence round-trip: an unknown top-level manifest key must survive a
# changed same-epoch cycle verbatim (the S4 contract this repo used to
# pin via the differential harness -- now pinned here against the
# installed package instead of local source).
# ---------------------------------------------------------------------------


_EPOCH_A = {"socket_path": "/tmp/tmux-1000/default", "server_pid": 111, "inode": 1}
_EPOCH_B = {"socket_path": "/tmp/tmux-1000/default", "server_pid": 222, "inode": 2}


def test_presence_round_trips_an_unknown_top_level_key():
    manifest = {
        "epoch": _EPOCH_A,
        "sessions": {"alpha": {"first_seen_at": 1.0, "last_seen_at": 1.0}},
        # An app-owned key tmux-kit's presence logic has never heard of --
        # it must round-trip verbatim, not be dropped by a closed-key-set
        # rebuild.
        "app_extra": {"muxplex-owned": True},
    }
    # A DIFFERENT epoch (new server) forces the cold-start rebuild branch --
    # the one most likely to silently drop an unrecognized top-level key.
    result, changed = _kit_presence.update_manifest(
        manifest,
        _EPOCH_B,
        ["alpha"],
        now=1_700_000_000.0,
        cwds={},
    )
    assert changed is True
    assert result.get("app_extra") == {"muxplex-owned": True}, (
        "tmux_kit.presence.update_manifest no longer round-trips an "
        "unknown top-level key verbatim -- this is the contract muxplex's "
        "manifest.py shim relies on to carry its own app-level state "
        "(e.g. rename_in_flight) alongside the library's core keys."
    )


if __name__ == "__main__":
    pytest.main([__file__])

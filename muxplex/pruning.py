"""
Local sidecar bookkeeping for stale-key pruning.

Stale-key pruning is a per-device concern: each device independently tracks
which session keys it has failed to observe, and prunes them from its own
settings once the grace period expires.  That bookkeeping must NEVER be synced
to peers — it is stored in a local sidecar file outside settings.json.

The prune ACTION (removing stale keys from view.sessions / hidden_sessions) IS
a normal settings write and DOES sync via the existing LWW mechanism.

See docs/plans/2026-05-17-hidden-state-redesign-design.md, Phase 4 and the
section "Stale key pruning (separate concern, local-only state)".
"""

import json
from pathlib import Path

from muxplex.settings import atomic_write_text

PRUNING_STATE_PATH = Path.home() / ".config" / "muxplex" / "pruning.json"


def load_pruning_state() -> dict:
    """Load local pruning bookkeeping from the sidecar file.

    Returns an empty dict on absent file or corrupt JSON — never raises for
    either condition.  Unexpected errors (e.g. PermissionError) propagate.

    The returned dict has the shape::

        {
            "first_missed_at": {
                "dev1:dead-session": 1747512345.0,
                ...
            }
        }
    """
    try:
        text = PRUNING_STATE_PATH.read_text(encoding="utf-8", errors="replace")
        data = json.loads(text)
        if not isinstance(data, dict):
            return {}
        return data
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, ValueError):
        return {}


def save_pruning_state(state: dict) -> None:
    """Atomically write pruning bookkeeping to the sidecar file.

    Creates parent directories as needed.  Format is unchanged (indent=2,
    trailing newline); only the *way* it reaches disk changed.

    This used to end in a bare ``PRUNING_STATE_PATH.write_text(...)`` -- no
    staging file, no ``os.replace()``, no fsync -- which made an interrupted
    write (crash, OOM, power cut, full disk) leave a truncated JSON file.
    ``load_pruning_state()`` above swallows ``JSONDecodeError`` and returns
    ``{}``, so that damage is not merely silent: it **erases its own
    evidence**.  The next poll cycle writes a clean, complete, wrong file.

    Wrong how: ``first_missed_at`` is the stale-key grace clock.  Losing it
    restarts the grace period for every session key at once, which changes
    WHEN real view pins get pruned -- a delayed, silent behaviour change with
    no error anywhere for anyone to notice.

    Delegates to ``settings.atomic_write_text`` rather than carrying a private
    copy of tmp + fsync + ``os.replace()``.  Four near-identical private
    implementations is how they drift apart, and this batch has already fixed
    two bugs whose root cause was exactly that shape.  What comes with the
    shared helper, beyond atomicity:

    * A UNIQUE staging name (``tempfile.mkstemp``), not a fixed
      ``pruning.json.tmp``.  This file has one writer process today (the
      server's poll cycle), so that is insurance rather than a fix for an
      observed race -- but it is the race muxplex-673 measured on the shared
      staging path in ``manifest.py``/``state.py``, and inheriting it means
      this file cannot acquire that bug later by growing a second writer.
    * The staging file lives in the target's own directory, because
      ``os.replace()`` is only atomic within a filesystem.
    * fsync of the contents before the rename, and of the directory after it.
    * A first-ever ``pruning.json`` is created 0600.  It holds no secret; that
      is simply the helper's default for a file with no prior mode to
      preserve, and an operator who widens it afterwards keeps that choice.
    """
    atomic_write_text(PRUNING_STATE_PATH, json.dumps(state, indent=2) + "\n")

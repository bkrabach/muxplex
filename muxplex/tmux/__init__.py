"""The tmux library boundary (extraction stage S1).

This subpackage holds the code that passes both of the plan's admission
tests (docs/plans/2026-08-08-tmux-lib-extraction-plan.md §7.1): *no import
from ``muxplex.*`` above it* (one scheduled exception, see below) and *no
muxplex-specific constant*. It is the internal boundary that stages S2/S3
turn into a standalone ``lib/`` workspace member a second application can
import without depending on the muxplex server package.

Modules (the §7.1 table):

    proc      -- run_tmux() / tmux_env() argv+env plumbing
    names     -- session-name validation (security boundary) + rename
    observe   -- epoch probe, enumeration, pane capture, snapshot caches
    presence  -- the manifest presence rule (pure functions, no I/O)
    bell      -- bell *detection* + the sole run-shell construction site
    keys      -- typed-input argv builders and the allowlist fence mechanism
    cgroup    -- the systemd cgroup-escape (the 44-session incident, packaged)

S1 is a PURE MOVE: every function here is byte-for-byte the code that lived
in ``sessions.py`` / ``bells.py`` / ``manifest.py`` / ``terminal_input.py``
/ ``cgroup_escape.py`` / ``main.py``, proven behaviour-identical by the
differential harness (``pytest -m differential``). The old module paths
re-export everything, so no caller changed.

Known, scheduled impurity: ``proc.py`` still reads muxplex's settings file
(``load_settings()`` -- the one wrong-way arrow in the import graph, plan
§1.1). Stage S2 inverts it into injected config; the §7.2 import-purity
rail lands with that inversion, not before.

What deliberately does NOT live here (plan §3.5, §16, confirmed against the
code): ``ttyd.py`` (its AF_UNIX lifecycle is second-tranche, gated on the
second app's embedded-terminal design; it also imports app-side
``STATE_DIR``), manifest I/O (``load_manifest``/``save_manifest`` default
to muxplex's ``STATE_DIR`` path until §13.3's injected-path shape),
``restore.py``'s policy, views, federation, follow-ups, and the
``Sender``/``SendPolicy`` send API (§15.1's future surface -- it does not
exist yet; building it is not a pure move).
"""

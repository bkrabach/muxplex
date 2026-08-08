"""The tmux library boundary (extraction stages S1 + S2).

This subpackage holds the code that passes both of the plan's admission
tests (docs/plans/2026-08-08-tmux-lib-extraction-plan.md §7.1): *no import
from ``muxplex.*`` above it* and *no muxplex-specific constant*. It is the
internal boundary that stage S3 turns into a standalone ``lib/`` workspace
member a second application can import without depending on the muxplex
server package.

Modules (the §7.1 table, plus §15.1's spawn):

    proc      -- run_tmux() / tmux_env() argv+env plumbing (config injected)
    spawn     -- spawn_session(name, caller-resolved template, env=...)
    names     -- session-name validation (security boundary) + rename
    observe   -- epoch probe, enumeration, pane capture, snapshot caches
    presence  -- the manifest presence rule (pure functions, no I/O)
    bell      -- bell *detection* + the sole run-shell construction site
    keys      -- typed-input argv builders and the allowlist fence mechanism
    cgroup    -- the systemd cgroup-escape (the 44-session incident, packaged)

S1 was a PURE MOVE, proven behaviour-identical by the differential harness
(``pytest -m differential``); the old module paths re-export everything, so
no caller changed. Stage S2 (plan §13.2 stage 3, §4.3) then inverted the
one wrong-way arrow S1 deliberately left in place: nothing here reads
muxplex's settings file any more. Configuration is INJECTED -- see
``proc.tmux_env(socket_dir)`` / ``proc.set_env_factory()`` /
``spawn.spawn_session(..., env=...)``; muxplex does its injecting in
``muxplex/sessions.py``, its app-side facade. The §7.2 import-purity rail
(``tests/test_safety_rails.py``) now enforces the boundary structurally: a
``muxplex.*`` import from inside this subpackage is a red test, not a code
review hope.

What deliberately does NOT live here (plan §3.5, §16, confirmed against the
code): ``ttyd.py`` (its AF_UNIX lifecycle is second-tranche, gated on the
second app's embedded-terminal design; it also imports app-side
``STATE_DIR``), manifest I/O (``load_manifest``/``save_manifest`` default
to muxplex's ``STATE_DIR`` path until §13.3's injected-path shape),
``restore.py``'s policy, views, federation, follow-ups, and the
``Sender``/``SendPolicy`` send API (§15.1's future surface -- it does not
exist yet; building it is not a pure move).
"""

"""tmuxkit -- async, argv-exec tmux session-management primitives.

Extracted from muxplex (extraction stages S1-S3,
docs/plans/2026-08-08-tmux-lib-extraction-plan.md in the muxplex repo).
This package holds the code that passes both of the plan's admission tests
(SS7.1): *no import from ``muxplex.*``* and *no muxplex-specific constant*.
S3 (SS13.2 stage 3.5) made it a standalone ``lib/`` uv workspace member --
named for tmux, not for muxplex (SS4.4) -- that a second application can
import without depending on the muxplex server package (no fastapi, no
uvicorn, no pam, no httpx: stdlib only, enforced by
muxplex/tests/test_lib_import_smoke.py).

Modules (the SS7.1 table, plus SS15.1's spawn):

    proc      -- run_tmux() / tmux_env() argv+env plumbing (config injected)
    spawn     -- spawn_session(name, caller-resolved template, env=...)
    names     -- session-name validation (security boundary) + rename
    observe   -- epoch probe, enumeration, pane capture, snapshot caches
    presence  -- the manifest presence rule (pure functions, no I/O)
    bell      -- bell *detection* + the sole run-shell construction site
    keys      -- typed-input argv builders and the allowlist fence mechanism
    cgroup    -- the systemd cgroup-escape (the 44-session incident, packaged)

S1 was a PURE MOVE, proven behaviour-identical by the differential harness
(``pytest -m differential`` in the muxplex repo); the old muxplex module
paths re-export everything, so no caller changed. S2 (plan SS13.2 stage 3,
SS4.3) inverted the one wrong-way arrow S1 deliberately left in place:
nothing here reads muxplex's settings file. Configuration is INJECTED --
see ``proc.tmux_env(socket_dir)`` / ``proc.set_env_factory()`` /
``spawn.spawn_session(..., env=...)``; muxplex does its injecting in
``muxplex/sessions.py``, its app-side facade. The SS7.2 import-purity rail
(``muxplex/tests/test_safety_rails.py``) enforces the boundary
structurally: ANY ``muxplex`` import from inside this package is a red
test, not a code review hope.

The library's tests -- every incident test that moved with its code -- live
in ``muxplex/tests/`` in the same repo, under that suite's autouse safety
rails (isolated SETTINGS_PATH, isolated TMUX_TMPDIR, neutralized port
killer); see SS7.3 rail 2.

What deliberately does NOT live here (plan SS3.5, SS16, confirmed against
the code): ``ttyd.py`` (its AF_UNIX lifecycle -- including the
``SOCKET_SUFFIX`` fence -- is second-tranche, gated on the second app's
embedded-terminal design; it also imports app-side ``STATE_DIR``), manifest
I/O (``load_manifest``/``save_manifest`` default to muxplex's ``STATE_DIR``
path until SS13.3's injected-path shape), ``restore.py``'s policy, views,
federation, follow-ups, and the ``Sender``/``SendPolicy`` send API
(SS15.1's future surface -- it does not exist yet; building it is not a
pure move).

Before running a second consumer on a host that also runs muxplex, read
plan SS17 (shared tmux server hazards: the global ``alert-bell`` hook slot,
presence cross-talk, fence overlap, name collisions).
"""

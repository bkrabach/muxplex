# Building on `tmuxkit`

`tmuxkit` is a reusable tmux session-management library extracted from muxplex so
other apps can manage tmux sessions the way muxplex does — **without forking that
code**. Fixes flow both ways: a change to `tmuxkit` reaches every consumer.

> **Status (2026-08-08):** `tmuxkit` currently lives inside the muxplex repo as a
> uv workspace member at `lib/`, version **0.44.0**, held at **0.x with no semver
> promise**. It is moving to its own public repo (`bkrabach/tmux-kit`) and PyPI;
> until then, depend on it via the git subdirectory form below. This file travels
> with the package to its new home.

## How to depend on it

**Today (from the muxplex repo subdirectory):**
```toml
dependencies = [
  "tmuxkit @ git+https://github.com/bkrabach/muxplex.git@v0.44.0#subdirectory=lib",
]
```

**After the repo split (PyPI + git+https):**
```toml
dependencies = ["tmuxkit>=0.44.0"]      # public installs resolve from PyPI
# For a pinned git install (e.g. locked/managed environments):
#   tmuxkit @ git+https://github.com/bkrabach/tmux-kit.git@v0.44.0
```

**Never copy `tmuxkit` source into your app.** A byte-similar copy IS the drift
incident the extraction exists to prevent. Need a change? Open a PR against the
`tmuxkit` repo — never a fork.

## The public surface (as shipped in 0.44.0)

Stdlib-only. Importing `tmuxkit` pulls in NO web server, no fastapi, no pam
(enforced by a smoke test).

| Module | What it gives you |
|--------|-------------------|
| `tmuxkit.proc` | `run_tmux()`, `tmux_env()`, `set_env_factory()`. **Config is injected, never read** — you install an env factory at startup; the lib never reads your settings file. |
| `tmuxkit.observe` | `enumerate_sessions()`, `probe_tmux_epoch()`, `capture_pane()` / `_metadata` / `_window`, session caches + getters (`get_session_cwds()`, ...). Scrollback paging via absolute-line params. |
| `tmuxkit.names` | `is_valid_session_name()`, `is_tmux_stable_name()`, `rename_tmux_session()`, `SESSION_NAME_RE`. |
| `tmuxkit.presence` | `update_manifest()`, `compute_restore_plan()`, `mark_restored()`. Owns the core presence keys; **your app writes its own keys beside them, in its own state dir** — unknown top-level keys round-trip verbatim (contract-tested). |
| `tmuxkit.bell` | `poll_bell_flag()`, `build_alert_bell_hook()`. |
| `tmuxkit.keys` | send-input argv builders + the permission fence (`input_allowed_for_session()`, `session_matches_allowlist()`, `redact_preview()`). |
| `tmuxkit.spawn` | `spawn_session(name, template, *, env)` — caller resolves the template. |
| `tmuxkit.cgroup` | `should_escape()`, `wrap_exec_argv()`, `environment_mode()` — the systemd `--scope` escape that keeps sessions alive past the launching unit. |

## NOT in the library yet — deferred until a consumer needs them

These are open **on purpose**. The library holds at 0.x precisely so the second
real consumer shapes them. If you need one, that's the signal to move it — say so.

- **`Sender` / `SendPolicy`** typed send-API. The argv builders + fence exist in
  `tmuxkit.keys`; the deny-by-default policy object is unbuilt.
- **ttyd / embedded-terminal lifecycle.** muxplex still owns it; the seam is
  defined (`§16` of the extraction plan) but not cut. It is gated on YOUR embedded
  human-UX design — how you want people to reach a session is the forcing function.
- **manifest file I/O with injected paths.** The presence *logic* is in the lib;
  the file read/write defaults still assume muxplex's dir.

## Hazards to design around (each learned from a real incident)

1. **Never render to a live pane.** The lib enforces exactly one `run-shell`
   construction site (a bell hook that writes nothing to the user's terminal). A
   diagnostic that painted a pane once spammed 53 live sessions.
2. **Two apps, one tmux server = silent failures.** The tmux `alert-bell` hook is
   a single GLOBAL slot — last writer wins, the loser's bells go dark with no
   error. And presence cross-talk: your app's cold start can offer to restore
   another app's sessions. Scope your observation; don't stomp the hook.
3. **ttyd's socket fence.** If/when you take the terminal path: the lib hands you a
   live AF_UNIX socket that structurally cannot become a TCP listener. Keep it that
   way — ttyd silently falls back to an unauthenticated `INADDR_ANY:7681` writable
   terminal on the LAN otherwise.
4. **The presence record is a POSITIVE record** — removed only by observed
   individual session death against a live, identity-matched server. Never a TTL,
   never a sweep. Adding one is how 52 sessions got lost.

## An environment gotcha that will bite you

`tmuxkit.cgroup.should_escape()` returns True only where there is a usable systemd
`--user` session (`XDG_RUNTIME_DIR` + working `systemd-run --user`). A container
without one silently takes a different spawn path, so a test env without systemd
`--user` will NOT exercise the scope-escape branch. If you test in a container,
know that branch is dead there and prove it separately.

## References

- **Reference implementation:** muxplex itself — read how it installs the env
  factory, sets its state dir, and drives restore.
- **Design + rationale + the deferred-surface decisions:**
  `muxplex/docs/plans/2026-08-08-tmux-lib-extraction-plan.md` (§13–17 cover the
  second-consumer case).
- **Presence-rule regression bed:** `pytest -m differential` — reuse it if you
  ever touch `tmuxkit`.

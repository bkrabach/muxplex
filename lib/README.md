# tmux-kit

Async, argv-exec tmux session-management primitives, extracted from
[muxplex](https://github.com/bkrabach/muxplex) (design:
`docs/plans/2026-08-08-tmux-lib-extraction-plan.md`; rename:
`docs/plans/2026-08-09-tmuxkit-own-repo-and-pypi-plan.md`).

Named for tmux, not for muxplex (plan §4.4): this is a library about a tmux
server, usable by any application that manages tmux sessions. muxplex is its
first consumer, as a uv workspace member of the same repo — one repo, one
commit, one version, one rollout (§14.2).

**stdlib only.** No fastapi, no httpx, no server code. Configuration is
injected, never read (§4.3): no function in this package knows that a
settings file exists.

## Modules

| Module | Provides |
|---|---|
| `tmux_kit.proc` | `run_tmux()`, `tmux_env(socket_dir)` — argv+env plumbing, `TmuxError` carrying tmux's stderr |
| `tmux_kit.spawn` | `spawn_session(name, resolved_template, env=...)` — cgroup-escaped, with the exists-after-nonzero-exit (TTY-attach) tolerance |
| `tmux_kit.names` | `SESSION_NAME_RE`, `is_valid_session_name`, `is_tmux_stable_name`, `rename_session` — security-load-bearing name validation |
| `tmux_kit.observe` | `probe_tmux_epoch`, `enumerate_sessions`, pane capture, snapshot caches |
| `tmux_kit.presence` | the manifest presence rule — pure functions, no I/O; unknown top-level keys round-trip verbatim (§13.3) |
| `tmux_kit.bell` | bell *detection* (`poll_bell_flag`) + `build_alert_bell_hook()` — the sole legal `run-shell` construction site, always silent |
| `tmux_kit.keys` | typed-input argv builders and the allowlist fence mechanism |
| `tmux_kit.cgroup` | `should_escape`, `wrap_exec_argv`, `wrap_shell_argv` — the 44-session systemd cgroup incident, packaged |

## Sharing one tmux server between two apps

The tmux server is a shared singleton and some of its state is a single
global slot. Before shipping a second consumer on a host that also runs
muxplex, read plan §17 — the `alert-bell` hook slot (last writer wins,
silently), presence cross-talk (scope your observations or your cold start
freezes the *other* app's sessions into your restore plan), fence overlap,
and session-name collisions.

## Versioning

Lockstep with the repo tag, 0.x semantics (§14.5), until the move to its own
repo and PyPI distribution (`tmux-kit`) lands. Consume it today as a git
dependency pinned to a tag — note the PyPI distribution name uses a hyphen
(`tmux-kit`) while the Python import package uses an underscore
(`tmux_kit`), because hyphens are illegal in Python identifiers:

```toml
dependencies = [
    "tmux-kit @ git+https://github.com/bkrabach/muxplex.git@v0.44.0#subdirectory=lib",
]
```

```python
import tmux_kit
```

Improvements flow both ways as PRs against this repo's `lib/` — never a
copy (§14.3: a file in a consumer that is byte-similar to a `lib/` file is
the incident, regardless of intent).

## Tests

The library's tests — every incident test that moved with its code — live in
`muxplex/tests/` in this repo, under the suite's autouse safety rails, and
run with the repo's normal `make test` flow. The differential harness
(`pytest -m differential`) is retained as the regression bed.

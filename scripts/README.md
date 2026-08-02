# scripts/

Standalone developer scripts. Not part of the package, not run by CI, not
imported by `muxplex/`. Two kinds live here:

- **Spike probes** (`spike_*.py`) — small experiments that answer one platform
  question with real processes. Kept in-repo so the answer can be re-verified
  on a new OS, a new ttyd, or a new tmux instead of being re-derived from
  scratch. `spike_bell_flag.py` is the original of the genre.
- **Build tooling** (`render-brand-assets.py`) — regenerates committed assets.

## The ttyd UNIX-socket probes

`spike_ttyd_*.py` and `spike_tmux_window_size.py` are the platform-verification
suite for the **per-session ttyd** architecture: giving every session its own
ttyd bound to its own UNIX domain socket, instead of today's single shared ttyd
on hardcoded TCP port 7682 (see AGENTS.md → "ttyd is loopback-only by design",
and `docs/plans/2026-08-01-per-device-sync-groups-plan.md` §0 for why the shared
process is a keystroke-misdirection hazard).

Together they establish that the transport works end to end. Re-run the whole
set when qualifying a new platform (WSL is next), a new ttyd release, or a new
tmux release.

| Probe | Question it answers |
|---|---|
| `spike_ttyd_unix_socket.py` | Does ttyd complete a **real RFC 6455 upgrade** over a UNIX socket — verified from first principles on a raw `AF_UNIX` socket, with no WebSocket library involved? |
| `spike_ttyd_relay.py` | Can a Python client speak ttyd's **actual wire protocol** over that socket — `AuthToken` handshake, `0x31` resize, `0x30` output/input — and get real PTY bytes back? |
| `spike_ttyd_session_isolation.py` | Do **two ttyds on two sockets attached to two sessions** stay isolated, with no cross-talk? This is the load-bearing one: it is the whole reason for the architecture. |
| `spike_tmux_window_size.py` | What does tmux's `window-size` do when **two differently-sized clients** attach to one session — does the phone shrink the laptop's view? |

`spike_ttyd_harness.py` is shared setup/teardown, not a probe.

### Running them

Each probe provisions its own tmux session and ttyd, so there is no setup step:

```bash
cd muxplex
.venv/bin/python scripts/spike_ttyd_unix_socket.py
.venv/bin/python scripts/spike_ttyd_relay.py
.venv/bin/python scripts/spike_ttyd_session_isolation.py
.venv/bin/python scripts/spike_tmux_window_size.py
```

Every probe exits `0` on the expected answer and non-zero otherwise, so the set
can be run as a smoke check on a new platform.

Requirements: real `ttyd` and `tmux` binaries on `PATH`, and the `websockets`
package (already a muxplex dependency — hence `.venv/bin/python`).
`spike_ttyd_unix_socket.py` needs neither `websockets` nor anything else beyond
the stdlib, on purpose.

To point a probe at a ttyd you started yourself, pass the socket path(s):

```bash
.venv/bin/python scripts/spike_ttyd_unix_socket.py /tmp/mine.sock
.venv/bin/python scripts/spike_ttyd_relay.py /tmp/mine.sock MYMARKER
.venv/bin/python scripts/spike_ttyd_session_isolation.py /tmp/a.sock /tmp/b.sock
```

Run them on a host or in a container that is **not** serving a live muxplex.
They never touch port 7682, port 8088, or the default tmux server — each one
uses its own `tmux -L` socket and kills only the exact PID it spawned (AGENTS.md
→ "NEVER broad-kill by process name") — but the general rule still applies.

### Findings so far

Verified on **Linux (ttyd 1.7.4)** and **macOS 26.6 arm64 (ttyd 1.7.7)**.
Per-session ttyd over UNIX domain sockets works on both: real WebSocket upgrade,
full wire protocol, and no cross-talk between concurrent sessions.

Two findings are load-bearing and are enforced in code in
`spike_ttyd_harness.py` rather than only written down here, because both fail
**silently**:

**1. ttyd only treats `-i <path>` as a UNIX socket if the path ends in `.sock`.**

Give it any other suffix and ttyd does *not* error. It logs
`iface ... DOESN'T EXIST`, falls back to listening on **TCP port 7681**, and
stays alive with exit status 0. That silently reopens the
unauthenticated-writable-terminal exposure that `-i 127.0.0.1` was added to
close — ttyd runs `-W` with no `-c` credential, so anyone who can reach that
port can type into the attached tmux session.

> **Post-launch verification must check that the socket file actually exists.
> Never trust ttyd's exit code or liveness as evidence that it bound a socket.**

This applies directly to any future `spawn_ttyd()` that takes a socket path.

**2. `sun_path` is short, so generated socket paths must be hashed, not named.**

| Platform | `sun_path` limit | Usable | Through ttyd |
|---|---|---|---|
| Linux | 108 bytes | 107 | 107 |
| macOS | 104 bytes | 103 | **102** |

Session names are arbitrary-length and user-chosen; interpolating one into a
socket path under a deep temp dir overruns the limit. Generate short hashed
names (`socket_path()` does this) and range-check the result.

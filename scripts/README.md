# scripts/

Standalone developer scripts. Not part of the package, not imported by
`muxplex/`. Three kinds live here:

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
set when qualifying a new platform, a new ttyd release, or a new tmux release.
All three target platforms — Linux, macOS, WSL2 — are now qualified; see
"Findings so far" for what each run has to clear, and for the two failure modes
that a passing exit code will not catch.

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

**All three target platforms are GO.** Per-session ttyd over UNIX domain
sockets works on each: a real WebSocket upgrade, the full wire protocol, and no
cross-talk between concurrent sessions.

| Platform | Kernel / OS | ttyd |
|---|---|---|
| Linux | — | 1.7.4 |
| WSL2 (Ubuntu 24.04) | `6.6.87.2-microsoft-standard-WSL2` | 1.7.4 |
| macOS 26.6 arm64 | — | 1.7.7 |

WSL2 matched the other two on every question, with no new capability gaps:
a real RFC 6455 upgrade whose `Sec-WebSocket-Accept` was recomputed
independently rather than taken on trust; PTY round-trip both in bulk and
char-by-char; two sessions on two sockets with no cross-talk; SIGTERM removes
the socket file; and a leftover stale socket file does not block a rebind.
(The last two were answered by scratch probes, not by anything committed
here — the committed set covers upgrade, wire protocol, isolation, and window
sizing.)

Three findings are load-bearing and are enforced in code in
`spike_ttyd_harness.py` rather than only written down here, because all three
fail **silently**:

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

Confirmed identical on WSL. Worth restating what the fallback costs there: on a
host where TCP 7681 happens to be free, that fallback *succeeds* — and opens
exactly the unauthenticated writable terminal this migration exists to close.

**2. `sun_path` is short, so generated socket paths must be hashed, not named.**

| Platform | `sun_path` limit | Usable | Through ttyd |
|---|---|---|---|
| Linux | 108 bytes | 107 | 107 |
| WSL2 | 108 bytes | 107 | 107 |
| macOS | 104 bytes | 103 | **102** |

WSL follows Linux here, not Windows — but **portable code should target the
macOS number (102)**, which is the only one that is safe everywhere.

Session names are arbitrary-length and user-chosen; interpolating one into a
socket path under a deep temp dir overruns the limit. Generate short hashed
names (`socket_path()` does this) and range-check the result.

**3. On WSL the socket MUST live on a Linux-native filesystem — and the failure
mode is worse than "it doesn't work."**

Under `/mnt/c` (DrvFs/9p) a raw `socket.bind()` fails immediately and cleanly:

```
bind_errno: 95      ENOTSUP: Operation not supported
```

But **ttyd does not treat that as fatal.** Launched with
`-i /mnt/c/.../x.sock` it emits

```
E: [null wsi]: lws_socket_bind: ERROR on binding fd 15 to ".../x.sock" (-1 95)
```

**continuously, roughly every 10ms, indefinitely** — a tight busy-retry loop
with no backoff and no self-termination. The process stays alive, never creates
the socket file, never falls back to TCP, and burns CPU until something kills it
from outside. It is silently non-functional while *looking* healthy: a liveness
check reports a happy process forever. The same paths on ext4 (`/tmp`) work
fine, round-trip included.

> **Design implication for the real implementation, on every platform:** the
> socket directory must be a Linux-native path, and a user-configurable temp
> dir must never be allowed to point at (or default to) a DrvFs mount. A
> `/mnt/*` prefix check before spawning ttyd is cheap insurance on top of the
> "verify the socket file exists" guard — it turns an unkillable-looking spin
> into a legible error at the point of configuration.

`socket_path()` enforces this in the harness: under WSL it refuses a socket dir
that resolves under `/mnt/`, rather than handing ttyd a path it will spin on.

### Shipped

The per-session-ttyd-over-UNIX-sockets architecture these probes qualified
has shipped (`muxplex/ttyd.py`, see `docs/plans/2026-08-02-per-session-ttyd-plan.md`). These probes
remain the platform-qualification gate: re-run them when qualifying a new
platform, a new ttyd release, or a new tmux release.

### What counts as proof that ttyd bound

Finding 1 already says never to trust exit code or liveness. WSL extends that
list by one more entry, and it is the one you'd most expect to be reliable:
**ttyd's own log is not proof either.**

Given a wrong-suffix path, ttyd printed

```
N:  Listening on port: 7681
```

**even though that TCP bind had failed** — port 7681 was already held by an
unrelated pre-existing service. ttyd logged success for something that did not
happen. Cross-referencing `/proc/net/tcp` (listening inode, state `0A`) against
`/proc/<pid>/fd` proved the listening socket belonged to the *other* process,
not to ttyd.

> Trust only the socket file's actual existence — or, for a TCP bind, the PID
> that actually owns the listening socket. **Not exit code, not liveness, not
> ttyd's own log line.**

### Operational gotchas

Neither is a ttyd or muxplex bug; both cost real time to rediscover.

- **Background a process over SSH with full fd redirection, always.** Without
  it the SSH channel hangs. First hit on macOS, reproduced verbatim on WSL:
  `cmd </dev/null >log 2>&1 &`. (GNU `timeout` *is* present on WSL, unlike
  macOS, so timeouts are available there.)
- **Dead tmux sockets used to accumulate.** `kill-server` stops the harness's
  tmux server but leaves its socket file under `/tmp/tmux-<uid>/`. Dead files
  only — no server process survives — but they piled up across unattended runs.
  `ttyd_session()`'s teardown now unlinks it (`tmux_socket_file()`).

## Container drift

`check_container_drift.py` — a guard, not a probe. Wired into `make check`.

### What it guards

Browser proof is this project's reality gate. Four separate times a change was
reported working on non-browser evidence and turned out dead, clobbered, or
wrong. But that gate only means something if **the tree being clicked is the
tree being committed.**

`muxplex-cxd` made the LAN twin's `/opt/muxplex` a real git checkout precisely
so edits would be reviewable and collisions visible. It then decayed silently:
work moved to host worktrees, the container was never re-synced, and browser
verification was done by hand-patching container files to approximate whatever
the branch held at the time. When someone finally looked (`muxplex-cky`) it was
**54 commits behind with 3198 lines of uncommitted hand-patching on top.**

Nothing detected that. A person happened to notice. This script is the machine
that notices instead.

### What it checks, and what it returns

Two things: container `HEAD` == host `HEAD`, and container tree clean.

| Exit | Meaning |
|------|---------|
| 0 | IN SYNC — verified |
| 1 | DRIFT — fails `make check` |
| 2 | Could not verify (no twin CLI, container down, no checkout) |

Exit 2 is **not** a soft pass. It is a third named state, printed as loudly as
DRIFT. `make check` tolerates it because a contributor with no LAN twin has no
container to be stale — but the check never prints a reassuring "in sync" it
has not earned.

Overridable: `MUXPLEX_CONTAINER` (default `muxplex-lan-twin`),
`MUXPLEX_CONTAINER_SRC` (default `/opt/muxplex`).

### Re-syncing when it fires

**First, find out what the drift actually is. The answer must arrive before the
risk does.** The container may hold the only copy of something. In `muxplex-cky`
7 of 8 modified files turned out to be hand-applied approximations of commits
that had since landed byte-for-byte identically — but the 8th held 12 lines that
existed in no commit on any ref, and would have been destroyed by a reflexive
`reset --hard`.

Prove it rather than assuming it. Hash each container file and search all of
history for that blob:

```sh
git hash-object <file-pulled-from-container>
for c in $(git rev-list --all); do
  git rev-parse "$c:muxplex/frontend/style.css" 2>/dev/null
done | grep -q <hash> && echo "already on the branch"
```

If a file matches no commit anywhere, diff it against HEAD and account for
**every** differing line before overwriting it. Stop and report anything unique
rather than folding it in on your own judgement.

Then, once the drift is understood — the container has no network route to the
host repo, so history moves in as a bundle:

```sh
git bundle create /tmp/muxplex.bundle poc/agent-chat-panel
git bundle verify /tmp/muxplex.bundle          # confirm complete history
amplifier-digital-twin file-push muxplex-lan-twin /tmp/muxplex.bundle /root/muxplex.bundle
amplifier-digital-twin exec muxplex-lan-twin -- bash -c \
  'cd /opt/muxplex && git fetch /root/muxplex.bundle \
     "refs/heads/poc/agent-chat-panel:refs/remotes/sync/poc/agent-chat-panel" \
   && git reset --hard <branch-head>'
```

Verify by comparing `git rev-parse HEAD^{tree}` on both sides — equal tree
hashes prove the working trees are identical, which `HEAD` alone does not.

Frontend files are static: **reload the page, do not restart the service.**
Restarting muxplex in the twin wipes the seeded `counter` / `logtail` / `sysmon`
tmux sessions.

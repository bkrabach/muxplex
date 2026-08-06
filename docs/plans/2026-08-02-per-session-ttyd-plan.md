# Implementation Specification — Per-Session ttyd over UNIX Domain Sockets

Status: **MERGED to `main` on 2026-08-02 (`431edce`..`ae8cda4`, five commits) —
NOT YET IN A RELEASE.** The newest tag, `v0.33.0`, predates all five, so an
installed muxplex is still running the single shared ttyd this document
replaces. Retained as an architectural decision record.

**Read §0 first — it is the most valuable section in the document.** It
corrects five claims in the originating brief; two of them would have caused a
silent production failure had they been designed around rather than caught.

Three constraints established here are load-bearing and permanent. Each was
learned from a *silent* failure across the Linux / macOS 26.6 arm64 / WSL2
spikes in `scripts/` — in every case ttyd stayed alive and non-functional
rather than erroring:

- **The socket path must end in `.sock`** (`ttyd.SOCKET_SUFFIX`). ttyd's
  UNIX-socket detection is suffix-based, and a non-`.sock` path does not fail —
  it silently falls back to TCP **7681** on `INADDR_ANY`, reopening the exact
  unauthenticated-writable-terminal exposure the old `-i 127.0.0.1` bind
  existed to close. This is why the path is never hand-built. §0.2, §2, §3.6.
- **The socket directory must be a Linux-native filesystem.** On a DrvFs path
  (and some NFS mounts) ttyd spins at ~10ms forever — alive, bound to nothing.
  Startup must abort with a legible ENOTSUP error rather than leave that
  process running. §3.2, §14.1.
- **The socket file proves a bind at spawn; it never proves liveness.** A
  `SIGKILL`ed ttyd leaves its socket behind forever and `Path.exists()` answers
  `True` for it. The liveness probe must be a real `AF_UNIX connect()`. Porting
  it as `exists()` reintroduces the reconnect-bounce bug that the pre-accept
  liveness check was written to fix. §0.2, §3.4.

§7 settled three guard decisions that are easy to re-litigate later without a
record of why:

| Guard | Decision |
|---|---|
| `409 terminal_conflict` on `/connect` | **Deleted** — with one ttyd per session there is no conflict to report; it can no longer fire. §7.1 |
| `WS 4409` | **Kept, redefined, narrower** — now "the session you didn't select," not "another group owns the terminal." §7.2 |
| `state["terminal_group"]` | Server-side **logic deleted**; the **wire field is kept** as informational provenance, because subtracting a documented public field buys nothing. §7.4 |

What is live in the code today: AGENTS.md → "ttyd is loopback-only by design …
now per-session, over AF_UNIX", and `docs/API_SEMANTICS.md` → "per-session
ttyd". This document is the architecture that retires the shared-ttyd
constraint recorded in `2026-08-01-per-device-sync-groups-plan.md` §0 (whose
header still describes that work as pending platform verification).

The source tree cites this document as **`docs/plans/2026-08-02-per-session-ttyd-plan.md`** — its
name in the workspace where it was written — from `muxplex/ttyd.py`,
`main.py`, `pyproject.toml`, `docs/API_SEMANTICS.md`, `scripts/README.md`,
`frontend/terminal.js`, and the test suite, usually by section number. Those
citations refer to this file.

---

## §0. READ THIS FIRST — corrections to the brief, and one hard external constraint

The brief is substantially correct and the design is workable. Five things in it are wrong,
incomplete, or missing, and two of them would have caused a silent production failure. They are
here at the top rather than being specified around.

### 0.1 ❌ The claim "`kill_ttyd()` ports over unchanged" is FALSE

Verified against `muxplex/ttyd.py:119-192`. `kill_ttyd()` is not one strategy, it is two, and only
the first has a per-session analogue:

| Part | Lines | Ports over? |
|---|---|---|
| Strategy 1 inner block: read PID, `SIGTERM`, poll 0.1s×20, unlink | `ttyd.py:158-181` | **Yes** — this ~20 lines is the reusable core |
| Strategy 1 outer: single fixed `TTYD_PID_PATH`, single `_active_process` global | `ttyd.py:36, 75, 151` | **No** — must become an N-keyed registry |
| Strategy 2: `_kill_pids_on_port(TTYD_PORT, SIGTERM)` + `sleep(0.3)` | `ttyd.py:186-189` | **No** — `lsof -ti :7682` cannot find an `AF_UNIX` socket holder. There is no port. This has no analogue and must be **deleted**. |
| `spawn_ttyd()`'s pre-spawn `_kill_pids_on_port(TTYD_PORT, SIGKILL)` | `ttyd.py:242-243` | **No** — same reason. Deleted. |

Deleting Strategy 2 is the *goal* (it is the dangerous sweep AGENTS.md documents as killing another
instance's ttyd), but it is load-bearing today: it is the **only** thing that reaps a ttyd whose PID
was never recorded. That safety net has to be replaced, not just removed — see §4.5.

Additionally, the brief's stated *justification* ("since SIGTERM already removes the socket") is a
non-sequitur. Whether SIGTERM unlinks the socket has nothing to do with whether the kill logic
ports; and it is only true on a *clean* exit. `SIGKILL`, a crash, or an OOM leaves the socket file
behind. Teardown must unlink unconditionally after the process is confirmed gone (§4.4).

### 0.2 ⚠️ "The socket file existing is proof of bind" is true at spawn and WRONG as a liveness probe

`scripts/README.md`'s finding is correct in its own scope: after we `unlink()` the path and then
spawn ttyd, the reappearance of that file is proof that *this* ttyd bound *this* path. That is the
spawn-readiness gate and it is right.

It is **not** a liveness check. A socket file left behind by a `SIGKILL`ed ttyd exists forever and
answers `Path.exists() == True` while nothing is listening. `_ttyd_is_listening()`
(`main.py:2329-2341`) is currently a real TCP `connect()`; its replacement must be a real `AF_UNIX`
`connect()`, **not** `exists()`. If a builder ports it as `exists()`, the WS proxy will accept a
browser connection, dial a dead socket, and fail — reintroducing exactly the reconnect-bounce bug
the current pre-accept liveness check was written to fix. Specified in §3.4.

### 0.3 🚫 `ttyd_port` CANNOT be removed from `POST /connect`'s response — external hard dependency

The brief says the API should not need to change, and argues it should be additive. Correct — but
there is a stronger constraint than "additive is preferred," and it is not in the brief:

```python
# client/muxplex_client/_protocol.py:144-148
def parse_connect_result(raw: Mapping[str, Any]) -> ConnectResult:
    return ConnectResult(
        active_session=raw["active_session"],
        ttyd_port=int(raw["ttyd_port"]),        # ← no .get(), no default
    )
```

`ttyd_port` is a **required** field with no default, typed `int` in `models.py:145`. This client is
vendored into `muxplex-deck`'s venv (`muxplex-deck/.venv/lib/python3.11/site-packages/muxplex_client/`).
Omitting the field, or returning `null`, raises `KeyError`/`TypeError` inside the Stream Deck sidecar.

**Therefore:** `TTYD_PORT = 7682` survives as a module constant whose *only* remaining purpose is
this wire field. It is no longer a bind port. It must be renamed in spirit but not in identifier
(the identifier is the contract), and its module comment must say so loudly so a future reader does
not "clean up the unused constant."

Verified: no code in `client/` dials `ttyd_port`; it is parsed and surfaced only. A third-party
client that *dials* it was already violating AGENTS.md's loopback-only rule and was already reaching
a ttyd attached to an arbitrary session; that breakage is accepted and called out in §10.

### 0.4 ➕ `websockets>=14.0` floor is required — `pyproject.toml` currently declares `>=11.0`

The proxy needs `websockets.asyncio.client.unix_connect(path, uri=...)`. Verified present in the
installed 16.0; it does not exist below 14.0 (the asyncio client namespace was introduced there).

```
$ .venv/bin/python -c "import websockets.asyncio.client as c; print(websockets.__version__, c.unix_connect)"
16.0 <function unix_connect ...>
```

`pyproject.toml:30` says `"websockets>=11.0"`. **Raise it to `>=14.0`.** This is *not* the version
floor AGENTS.md deliberately rejected — that rejection was about pinning a floor to force one
particular implementation for one known bug, where `test-latest-deps` was the general fix. This is a
hard API requirement: below 14.0 the import fails outright. Note in the commit message that
`test-latest-deps` (which bypasses `uv.lock`) is what will actually prove the floor is right.

### 0.5 ➕ `WS /terminal/ws` has no session parameter — the change requires one, on two routes

The brief asks how the proxy reaches a UNIX socket "for both the local and federation relay paths."
The federation path needs **no socket work at all**: `federation_terminal_ws_proxy`
(`main.py:2727-2851`) dials `ws(s)://<remote>/terminal/ws` — the remote muxplex's own HTTPS endpoint
— never a ttyd. It is a muxplex-to-muxplex relay.

What it *does* need is the thing neither route has today: **a session name**. Today
`/terminal/ws` relays "the" ttyd, whose session is implicit in `state["terminal_session"]`. With N
ttyds there is no "the." Both routes take a new optional `?session=` query param; the federation
proxy forwards it upstream. Purely additive; absent → today's behavior exactly. Specified in §6.

### 0.6 ✅ Verified good news: `systemd-run --scope` execs in place, so `proc.pid` IS ttyd's pid

I was concerned that under cgroup escape (`ttyd.py:265-266`) the recorded PID would be
`systemd-run`'s, making PID-file teardown useless once the port sweep is deleted. Probed empirically
on this host today, using a uniquely-named copy of `/bin/sleep` and signalling only the exact
captured PID:

```
recorded pid (what ttyd.py writes to its PID file): 2728618
recorded pid cmdline: /tmp/mxprobe-cb6719e2-sleep 300      ← systemd-run exec'd in place
--- os.kill(recorded_pid, SIGTERM) ---
real workload processes after SIGTERM: []
VERDICT: SIGTERM PROPAGATED to the wrapped command
```

This matches `cgroup_escape.py:210`'s own comment. So a single PID-based teardown path is correct on
both the wrapped and unwrapped spawn. No systemd-specific teardown branch is needed.

One consequence worth encoding: `os.kill(pid, 0)` returns `True` for a **zombie**. For our own
children use `await proc.wait()`; reserve `os.kill(pid, 0)` for orphans adopted from a previous
process (§4.5).

### 0.7 ℹ️ Idle ttyds are cheap — the resource ceiling is hygiene, not correctness

The obvious worry with 51 sessions is "51 ttyds each holding a `tmux attach` client, and attached
client size drives tmux's `window-size` negotiation." That worry is **unfounded**: ttyd spawns its
command **per WebSocket client**, not at startup. In-repo evidence, `terminal.js:130-132`:

> `'tty' subprotocol is REQUIRED — without it ttyd never starts the PTY. Confirmed via raw Python
> WebSocket tests: ttyd accepts the TCP upgrade but sits completely silent (no child process
> spawned) when subprotocol is omitted.`

An idle ttyd (zero relays) holds a listening socket and nothing else: no tmux client, no PTY, no
window-size influence. So the ceiling and the idle reaper in §4.6 are resource hygiene (~3 MB and a
handful of fds each), not a correctness fix. Do not oversell them.

---

## §1. Scope

**In scope.** One ttyd per session, bound to a UNIX domain socket. Rewrite of `muxplex/ttyd.py`.
Changes to `connect_session`, `delete_current_session`, `terminal_ws_proxy`,
`federation_terminal_ws_proxy`, the poll loop, and lifespan in `muxplex/main.py`. Additive
`?session=` on two WS routes. Retirement of the single-owner terminal guard. `terminal.js` URL
change. Migration + rollback. Tests.

**Out of scope, explicitly.** `/api/state`'s field set (unchanged). Sync-group semantics for
`active_view`/`active_remote_id` (unchanged). tmux `window-size` policy (unchanged — two devices on
one session share one ttyd and behave exactly as today). `terminal_input.py` and the `/input` fence
(untouched). TLS, auth, federation key handling (untouched). Removing the frontend's 800 ms settle
sleep (§9.3 — deliberately deferred, version-skew hazard).

**Net code change must be negative.** Deleted: `_kill_pids_on_port`, both port sweeps, the `lsof`
dependency, `TTYD_BIND_ADDRESS`, the `/connect` 409 gate, the `/connect` same-session short-circuit,
`_prepare_ttyd_for_reconnect`'s fixed 0.8 s sleep, the poll loop's `terminal_group` release branch.
Added: socket-path derivation, a keyed registry, an idle reaper, one query param on two routes.

---

## §2. Design summary

```
                       ┌──────────────────── one per SESSION ───────────────────┐
browser ──WS──▶ muxplex /terminal/ws?session=X ──AF_UNIX──▶ ttyd(X) ──▶ tmux attach -t X
browser ──WS──▶ muxplex /terminal/ws?session=Y ──AF_UNIX──▶ ttyd(Y) ──▶ tmux attach -t Y
                       └───── independent; zero interaction; not a conflict ─────┘

peer   ──WSS─▶ muxplex /federation/{dev}/terminal/ws?session=X
                  └──WSS──▶ remote muxplex /terminal/ws?session=X ──AF_UNIX──▶ remote ttyd(X)
                     (federation relay never touches a ttyd socket — muxplex-to-muxplex only)
```

Socket: `<state_dir>/ttyd/mx-<sha256(session)[:12]>.sock`, dir mode `0700`, bind-probed at startup.
Lifecycle: idempotent `ensure_ttyd(session)` on `/connect` and on WS attach; refcounted relays;
idle-reap after 60 s at zero relays; hard cap 32; startup orphan reap from per-session run files.

---

## §3. `muxplex/ttyd.py` — full rewrite

Module docstring must carry forward, verbatim in substance: the unauthenticated-writable-terminal
security record currently at `ttyd.py:40-69`, the `0.0.0.0` incident, and the never-PATCHable rule.
`TTYD_BIND_ADDRESS` is deleted; **its rationale is not.** Rewrite it to state that `AF_UNIX`
supersedes `-i 127.0.0.1` with a strictly stronger confinement (filesystem permissions, no network
namespace at all), and that **`SOCKET_SUFFIX` is the successor to that fence**: a non-`.sock` path
makes ttyd fall back to TCP `7681` on `INADDR_ANY`, which is the *identical* exposure the `-i` flag
was added to close.

### 3.1 Constants

```python
from muxplex.state import STATE_DIR          # no import cycle: state.py imports only stdlib

TTYD_SOCKET_DIR: Path       # see 3.2
SOCKET_SUFFIX: str = ".sock"        # ttyd's UNIX-socket detection is suffix-based. Finding 1.
SOCKET_PREFIX: str = "mx-"
SOCKET_HASH_LEN: int = 12           # hex chars of sha256; basename is fixed-width
SOCKET_BASENAME_LEN: int = 20       # len("mx-") + 12 + len(".sock") — must equal the above
SUN_PATH_BUDGET: int = 102          # macOS-through-ttyd; the only value safe on all 3 platforms
DRVFS_MOUNT_PREFIX: str = "/mnt/"
SPAWN_READY_TIMEOUT: float = 5.0
SPAWN_POLL_INTERVAL: float = 0.005
TERM_POLL_INTERVAL: float = 0.05
TERM_TIMEOUT: float = 2.0
MAX_TTYDS: int = 32                 # backstop against a reaper bug, not user behavior
IDLE_REAP_SECONDS: float = 60.0

# LEGACY WIRE FIELD ONLY. ttyd no longer binds a TCP port at all. This constant exists
# solely because POST /api/sessions/{name}/connect returns `ttyd_port`, and
# client/muxplex_client/_protocol.py:147 reads it as a REQUIRED int with no default —
# omitting it raises KeyError inside muxplex-deck. DO NOT DELETE AS UNUSED.
TTYD_PORT: int = 7682

# One-time migration target. The pre-per-session ttyd wrote its PID here.
LEGACY_TTYD_PID_PATH: Path = Path(
    os.environ.get("TMUX_WEB_STATE_DIR", Path.home() / ".local" / "share" / "tmux-web")
).expanduser() / "ttyd.pid"
```

### 3.2 Socket directory

```python
def ttyd_socket_dir() -> Path
```

Resolution order (first match wins), each `.expanduser()`d:

1. `os.environ["MUXPLEX_TTYD_SOCKET_DIR"]` — the sun_path escape hatch.
2. `STATE_DIR / "ttyd"` (default: `~/.local/share/muxplex/ttyd`).

**Not a settings key, deliberately.** A settings key naming a filesystem path the server writes and
then connects to would have to join `settings.LOCAL_ONLY_KEYS` under AGENTS.md's rule (same class as
`tmux_socket_dir`, `tls_cert`). An env var sidesteps the whole `LOCAL_ONLY_KEYS`/`SYNCABLE_KEYS`
question, cannot be reached by a federation Bearer-key holder at all, and matches the existing
`MUXPLEX_STATE_DIR` / `TMUX_WEB_STATE_DIR` precedent. Anchoring the default on `STATE_DIR` also
means a scratch instance with a scratch `HOME` gets an isolated socket dir for free — which is the
direct fix for AGENTS.md's "an unpatched second instance WILL kill the first instance's ttyd."

```python
class TtydSocketDirError(RuntimeError): ...

def validate_socket_dir(directory: Path) -> None
```

Called **once at startup**, before the poll loop. Raises `TtydSocketDirError` with an actionable
message on any failure. Fail loud; never degrade. Checks, in order:

1. `hasattr(socket, "AF_UNIX")` — else raise (`muxplex requires AF_UNIX; this platform has none`).
2. `mkdir(parents=True, exist_ok=True)` then unconditional `chmod(0o700)`. (`mkdir`'s `mode=` is
   umask-masked and silently skipped when the dir already exists; `chmod` is not.)
3. `st = directory.lstat()` — reject if `stat.S_ISLNK(st.st_mode)`.
4. `st.st_uid != os.getuid()` → reject. `st.st_mode & 0o077` → reject. **This dir hosts
   unauthenticated writable terminals**; group/other access is a full RCE handoff.
5. `resolved = directory.resolve()`; if `is_wsl() and str(resolved).startswith(DRVFS_MOUNT_PREFIX)`
   → reject, message naming ENOTSUP/95 and ttyd's ~10 ms forever-retry loop. Resolve first so a
   symlink or `..` into `/mnt` is caught.
6. `len(str(resolved)) + 1 + SOCKET_BASENAME_LEN > SUN_PATH_BUDGET` → reject, reporting the actual
   length, the budget, and `MUXPLEX_TTYD_SOCKET_DIR` as the fix. (Default is 40 + 1 + 20 = 61 bytes;
   dir may be up to 81.)
7. **Bind probe — the real proof.** `bind()` a throwaway `AF_UNIX` socket at
   `resolved / ".probe.sock"`, then `close()` + `unlink(missing_ok=True)`. Any `OSError` → reject,
   including `errno` and `strerror`.

Step 7 is why step 5 is only a courtesy. The `/mnt/` prefix check is a *heuristic* that turns one
known-bad case into a legible message; the bind probe is *evidence*, and it catches every other
filesystem that cannot host an `AF_UNIX` socket (a DrvFs mounted elsewhere, some NFS
configurations, exotic overlays) that no prefix list would know about. This is the same lesson as
"trust the socket file, not ttyd's log": test the actual thing, not a proxy for it.

`is_wsl()` — copy from `scripts/spike_ttyd_harness.py`: `"microsoft" in platform.uname().release.lower()`.

### 3.3 Socket path derivation

```python
def socket_path_for(session_name: str) -> Path:
    digest = hashlib.sha256(session_name.encode("utf-8")).hexdigest()[:SOCKET_HASH_LEN]
    return TTYD_SOCKET_DIR / f"{SOCKET_PREFIX}{digest}{SOCKET_SUFFIX}"
```

Pure, no I/O, no validation (the directory was validated once at startup and the basename is
fixed-width, so per-call re-checking is dead weight). Hash, never interpolate: session names are
arbitrary-length and user-chosen.

**Collision handling.** 48 bits over 51 sessions gives a birthday probability ≈ 5×10⁻¹². Negligible
— but "negligible" is not "enforced," and a silent collision means two sessions sharing one ttyd,
i.e. exactly the keystroke-misdirection hazard this whole change exists to kill. `spawn_ttyd()`
therefore checks the registry for another live entry claiming the same path and raises
`TtydSpawnError` naming both session names. One dict scan; turns an astronomically-unlikely silent
cross-attach into a legible crash.

The hash means the filename does not reveal the session name. Compensate with a single
`logger.info("ttyd: spawned session=%r socket=%s pid=%d", ...)` at spawn and the session name
recorded in the run file (§3.5). **Do not** add `ttyd_socket_dir` to `GET /api/instance-info`:
that endpoint is unauthenticated, and unlike `tmux_socket_dir` (which callers *need* in order to
land sessions correctly) nothing outside this process needs the socket dir. Publishing the location
of a directory full of unauthenticated writable terminals is a recon aid with no compensating use.

### 3.4 Liveness

```python
def socket_is_live(path: Path) -> bool
```

Exact replacement for `main.py:2329-2341`'s `_ttyd_is_listening()`, same contract (never raises,
always returns a bool, sub-millisecond on success):

```python
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(0.5)
try:
    s.connect(str(path))
    return True
except (OSError, TimeoutError):     # FileNotFoundError, ConnectionRefusedError both land here
    return False
finally:
    s.close()
```

**A stale socket file gives `ConnectionRefusedError` → `False`.** That is the entire point (§0.2).
Never use `Path.exists()` for liveness. `exists()` appears exactly once in this module: the
spawn-readiness poll in §3.6, where we control the precondition by unlinking first.

### 3.5 Registry and run files

```python
@dataclass
class TtydProc:
    session: str
    socket_path: Path
    run_path: Path                              # <sockdir>/mx-<hash>.json
    pid: int
    proc: asyncio.subprocess.Process
    relays: int = 0
    idle_since: float | None = None             # time.monotonic(); None while relays > 0
    started_at: float = 0.0                     # time.monotonic()

_ttyds: dict[str, TtydProc] = {}                # keyed by session name
```

Run file, written after a successful readiness check, JSON, `encoding="utf-8"`:

```json
{"pid": 12345, "session": "amplifier-core", "socket": "/home/u/.local/share/muxplex/ttyd/mx-a1b2c3d4e5f6.sock"}
```

The session name and socket path are recorded (not merely derivable) so the orphan reaper can log
what it is reaping and can detect a hash mismatch without inverting the hash. This replaces the
single `TTYD_PID_PATH`.

### 3.6 Spawn

```python
class TtydSpawnError(RuntimeError): ...

async def spawn_ttyd(session_name: str) -> TtydProc      # unconditional
async def ensure_ttyd(session_name: str) -> TtydProc     # idempotent; the normal entry point
```

`ensure_ttyd(session)`:

1. `proc = _ttyds.get(session)`. If present and `socket_is_live(proc.socket_path)` → return it.
2. If present and not live → `await kill_ttyd(session)` (it died under us), fall through.
3. If `len(_ttyds) >= MAX_TTYDS` → `await reap_idle_ttyds(force_one=True)`. If still at the cap
   (every ttyd has `relays > 0`) → raise `TtydCapacityError`.
4. `return await spawn_ttyd(session)`.

`spawn_ttyd(session)`:

1. `sock = socket_path_for(session)`.
2. Registry collision guard: if any `p in _ttyds.values()` has `p.socket_path == sock` and
   `p.session != session` → `TtydSpawnError`.
3. `sock.unlink(missing_ok=True)` — **required**. `bind()` fails `EADDRINUSE` on an existing path
   regardless of liveness, and unlinking first is what makes the readiness poll in step 6 a valid
   proof of *this* process's bind. A leftover orphan still listening on the old inode becomes
   harmlessly unreachable (verified in the spikes: a stale socket file does not block a rebind).
4. Build argv — **exactly the shape the three platform spikes qualified**
   (`scripts/spike_ttyd_harness.py:230-244`). Note `-p` and `-i 127.0.0.1` are **gone**; `-i` now
   carries the socket path:

   ```python
   argv = ["ttyd", "-W", "-m", "3", "-i", str(sock), "tmux", "attach", "-t", session]
   if await should_escape():
       argv = wrap_exec_argv(argv)          # PRESERVE — see below
   ```

   The `should_escape()` / `wrap_exec_argv()` wrapping at `ttyd.py:265-266` **must be preserved
   verbatim.** ttyd's per-client `tmux attach` can still be what creates the tmux server, and cgroup
   membership is inherited by that child. AGENTS.md's 44-lost-sessions incident is the cost of
   getting this wrong. Per §0.6, `proc.pid` remains ttyd's own pid under the wrapper.

5. `asyncio.create_subprocess_exec(*argv, stdout=DEVNULL, stderr=DEVNULL,
   start_new_session=True, env=tmux_env())` — all four kwargs unchanged from `ttyd.py:268-277`.
6. **Readiness gate.** Loop until `deadline = monotonic() + SPAWN_READY_TIMEOUT`:
   - `sock.exists()` → break (bound).
   - `proc.returncode is not None` → raise `TtydSpawnError(f"ttyd exited {rc} before creating {sock}")`.
   - `await asyncio.sleep(SPAWN_POLL_INTERVAL)`.

   On timeout, kill the process and raise `TtydSpawnError` with **this exact diagnosis**:

   > `ttyd is running (pid N) but <sock> does not exist. It almost certainly fell back to TCP :7681
   > — an unauthenticated writable terminal. Liveness and exit status are NOT proof a socket was
   > bound, and neither is ttyd's own log.`

   This replaces `_prepare_ttyd_for_reconnect`'s fixed `sleep(0.8)` (`main.py:2485`). Typical
   readiness is 20-100 ms, so this is also a latency win.
7. Confirm with `socket_is_live(sock)`; if `False` raise `TtydSpawnError`. (Belt-and-braces: catches
   the pathological "file appeared, listener didn't.")
8. `chmod(sock, 0o600)`. The directory is already `0700`; this is defense in depth for a
   misconfigured umask or an operator who loosens the dir.
9. Write the run file, register in `_ttyds`, `idle_since = monotonic()`, log, return.

**Error behavior is loud everywhere.** `spawn_ttyd` never returns a half-working ttyd and never
swallows. Callers translate: `/connect` → HTTP 500 (§6.2); WS proxy → log + close (§5.1).

### 3.7 Teardown

```python
async def kill_ttyd(session_name: str) -> bool
async def kill_all_ttyd() -> int
```

`kill_ttyd(session)`:

1. `proc = _ttyds.pop(session, None)`. If `None` → return `False`.
2. `os.kill(proc.pid, SIGTERM)`, swallowing `ProcessLookupError`.
3. `await asyncio.wait_for(proc.proc.wait(), timeout=TERM_TIMEOUT)`. On `TimeoutError`:
   `os.kill(proc.pid, SIGKILL)`, `await proc.proc.wait()`.
   Use `proc.wait()`, **not** an `os.kill(pid, 0)` poll — the latter returns `True` for a zombie and
   would spin the full timeout every time (§0.6).
4. `proc.socket_path.unlink(missing_ok=True)`; `proc.run_path.unlink(missing_ok=True)`.
   Unconditional: a clean SIGTERM removes the socket, a SIGKILL does not, and we must not depend on
   which one happened.
5. Log at info; return `True`.

`kill_all_ttyd()` → `asyncio.gather(*(kill_ttyd(s) for s in list(_ttyds)))`, returns the count.
Called from lifespan shutdown.

### 3.8 Orphan reap (startup)

```python
async def reap_orphan_ttyds() -> int
```

Replaces `kill_orphan_ttyd()` (`ttyd.py:195-209`). Runs once in lifespan, **before** the poll loop.

1. Take **one** process snapshot: `ps -eo pid=,command=` via `create_subprocess_exec`, 5 s timeout,
   parsed into `dict[int, str]`. One call total — not one per orphan.
2. For each `TTYD_SOCKET_DIR.glob("mx-*.json")`:
   - Parse. On malformed JSON → unlink the run file and its sibling `.sock`, log a warning, continue.
   - **Identity check — mandatory.** Signal only if `pid in snapshot` **and** `"ttyd" in cmdline`
     **and** `record["socket"] in cmdline`. Otherwise: unlink both files, log
     `"ttyd: stale run file for session=%r (pid %d not ours); removed without signalling"`, continue.
   - Passing → SIGTERM, poll `os.kill(pid, 0)` every `TERM_POLL_INTERVAL` up to `TERM_TIMEOUT`,
     SIGKILL if needed, unlink both files, count it.
3. Also unlink any `mx-*.sock` with no matching `mx-*.json` (a crash between bind and run-file write).
4. Return the count.

**Why the identity check is not optional.** Run files survive a reboot; their PIDs are then almost
certainly recycled into unrelated processes. Today's single-PID-file version has this bug once; a
naïve per-session port would have it 51 times, in a repo whose incident history is dominated by
"we killed the wrong thing." The check is precise — we key by our *recorded* PID and use the command
line only to *confirm identity*, which is categorically different from AGENTS.md's forbidden
`pkill -f ttyd` name search. On a failed match we **do not signal**: an unkillable orphan is a
leak (it holds an unlinked inode and its tmux client), a mis-signalled innocent process is a
catastrophe. Fail safe, log loud.

If `ps` is unavailable or times out, treat every record as failing the check (unlink, don't signal)
and log an error.

### 3.9 Legacy reap (one-time migration)

```python
async def reap_legacy_ttyd() -> bool
```

Runs once in lifespan, immediately after `reap_orphan_ttyds()`.

1. If `LEGACY_TTYD_PID_PATH` does not exist → return `False`.
2. Read the int (invalid content → unlink, return `False`).
3. **Same identity check**, against the same `ps` snapshot: `"ttyd" in cmdline` and `"-p 7682"` or
   `":7682"` in cmdline. Pass → SIGTERM, poll, SIGKILL, unlink. Fail → unlink only.
4. **Then, unconditionally, detect-and-report — never sweep.** If
   `socket.create_connection(("127.0.0.1", 7682), timeout=0.5)` succeeds, log at **ERROR**:

   > `A process is still listening on 127.0.0.1:7682. This is very likely a pre-upgrade muxplex
   > ttyd — an UNAUTHENTICATED WRITABLE TERMINAL attached to a live tmux session. muxplex no longer
   > manages that port and will not kill an unidentified process. Identify and stop it manually:
   > lsof -ti :7682`

   Detect and report; do **not** kill. Killing an unidentified PID on a hardcoded port is precisely
   the dangerous sweep this migration exists to delete, and it is the mechanism AGENTS.md documents
   as destroying another instance's ttyd. An honest loud log is the correct floor here: the failure
   mode we must not have is a *silent* leftover writable terminal.

### 3.10 Relay accounting and the idle reaper

```python
def acquire_relay(session_name: str) -> None      # relays += 1; idle_since = None
def release_relay(session_name: str) -> None      # relays = max(0, relays-1); if 0: idle_since = monotonic()
def relay_count(session_name: str) -> int         # 0 for an unknown session
async def reap_idle_ttyds(*, force_one: bool = False) -> list[str]
def ttyd_stats() -> dict                          # {"count": int, "sessions": [{session, relays, idle_s}]}
```

All three accessors are total: unknown session → no-op / `0`, never a raise. The ttyd can be reaped
out from under a racing relay, and a `KeyError` in a `finally` block would mask the real error.

`reap_idle_ttyds()`:
- Normal mode: kill every entry with `relays == 0` and
  `monotonic() - idle_since >= IDLE_REAP_SECONDS`.
- `force_one=True`: if nothing qualifies on age, kill the single **oldest-idle** entry with
  `relays == 0`. Returns `[]` if every entry is busy — the caller raises `TtydCapacityError`.

Called from `_run_poll_cycle()` — **no new timer**, riding the existing ~1 s cycle exactly as
`gc_sync_groups()` rides `prune_devices()`.

A ttyd spawned by `/connect` for a client that never opens a WS has `idle_since` set at spawn, so it
is reaped 60 s later. Correct: the client either opens its WS within ~800 ms or it is not coming.

Reaping is safe because a ttyd is a *view*, not the durable thing: killing it detaches its `tmux
attach` clients and leaves the tmux session untouched. Respawn on next attach is ~20-100 ms, well
inside the client's existing settle delay.

---

## §4. Where the sockets live — summary

| | |
|---|---|
| Default dir | `~/.local/share/muxplex/ttyd/` (`STATE_DIR / "ttyd"`) |
| Override | `MUXPLEX_TTYD_SOCKET_DIR` env var only (never a settings key — §3.2) |
| Dir mode | `0700`, uid-checked, symlink-rejected, bind-probed at startup |
| File | `mx-<sha256(session)[:12]>.sock`, mode `0600`, 20-byte fixed basename |
| Sibling | `mx-<same-hash>.json` — run record (pid, session, socket) |
| Budget | `len(dir) + 21 ≤ 102`; default is 61 |

---

## §5. WebSocket proxies

### 5.1 `terminal_ws_proxy` (`main.py:2490-2682`)

Signature: `async def terminal_ws_proxy(websocket, device_id: str | None = None, session: str | None = None)`.

Order of operations — the pre-accept structure is load-bearing and must be preserved:

1. `_ws_auth_check()` — unchanged.
2. **Resolve the target session** (new, replaces the implicit global):
   ```
   state = load_state()                       # under state_lock
   group  = resolve_group(state, device_id)   # KeyError → _accept_then_close(4404); unchanged
   target = session if session is not None else state["terminal_session"]
   ```
   - `target is None` → `_accept_then_close(4404)`, return.
   - `not is_valid_session_name(target)` → `_accept_then_close(4404)`, return.
   - `target not in get_session_list()` → `_accept_then_close(4404)`, return. (Fail-closed exact
     membership, same pattern as `connect`/`delete`/`input`. An empty cache rejects everything.)
3. **The §0-hazard backstop, redefined** (see §7.2): if `device_id is not None` and
   `read_group_state(state, group)["active_session"] != target` → `_accept_then_close(4409)`, return.
4. Register in `_ws_proxy_tasks` — **unchanged** (`main.py:2582-2585`).
5. Readiness. Replace the `_ttyd_is_listening()` check + `_prepare_ttyd_for_reconnect` race with the
   same structure over the new primitive:
   ```
   if not socket_is_live(socket_path_for(target)):
       await websocket.receive()                       # consume ASGI connect — unchanged, load-bearing
       prep = asyncio.create_task(_prepare_ttyd(target))
       disc = asyncio.create_task(_client_disconnected(websocket))
       done, pending = await asyncio.wait({prep, disc}, return_when=FIRST_COMPLETED)
       ... cancel pending, gather(return_exceptions=True) ...
       if disc in done: discard task; return          # skip accept() — unchanged
       if prep.exception() or not prep.result(): discard; return
   ```
   `_prepare_ttyd(target) -> bool` replaces `_prepare_ttyd_for_reconnect()` (`main.py:2464-2487`):
   `await ensure_ttyd(target)`, return `True`; on `TtydSpawnError`/`TtydCapacityError` log at
   **warning** with the session name and return `False`. It no longer reads state (the caller
   resolved the target) and no longer sleeps.

   The `_client_disconnected` race, the `await websocket.receive()` that feeds it, and the
   skip-`accept()` bail-out all stay **exactly as they are**. `_client_disconnected`'s docstring must
   be updated only where it names `kill_ttyd/spawn_ttyd/sleep(0.8)` as the window.
6. `await websocket.accept(subprotocol="tty")` — unchanged.
7. `acquire_relay(target)` **immediately before the `try:`** whose `finally` calls
   `release_relay(target)` — so a dial failure cannot leak a refcount and pin a ttyd against the
   idle reaper forever. Then dial:
   ```python
   from websockets.asyncio.client import unix_connect
   async with unix_connect(
       str(socket_path_for(target)),
       uri="ws://localhost/ws",
       subprotocols=[Subprotocol("tty")],
   ) as ttyd_ws:
   ```
   `uri` is **required**: `unix_connect`'s default is `ws://localhost/`, which sends request path
   `/` — ttyd's WebSocket endpoint is `/ws` (today's `main.py:2628`). Verified against a real
   `unix_serve` on a real socket:

   ```
   $ unix_connect(sock, uri="ws://localhost/ws", subprotocols=["tty"])
   server saw: {'path': '/ws', 'host': 'localhost', 'subproto': 'tty'}
   ```

   The `tty` subprotocol is non-negotiable — without it ttyd upgrades and then sits silent, never
   spawning the PTY (`terminal.js:130-132`).
8. Relay body — `client_to_ttyd` / `ttyd_to_client` and the
   `asyncio.wait(FIRST_COMPLETED)` + cancel-pending + `gather(return_exceptions=True)` block
   (`main.py:2634-2673`) are copied **byte-for-byte**. Do not "simplify" to `gather` — AGENTS.md's
   clean-shutdown section and that function's own comment explain the hang.
9. `finally:` — add `release_relay(target)`; keep `_ws_proxy_tasks.discard(_task)` and the guarded
   `websocket.close()` exactly as-is.

Close-code policy: **no new codes.** `4404` = "target not resolvable" (now covering unknown
`device_id`, missing/invalid/unknown session). `4409` = the redefined selection-mismatch backstop.
`4001` = auth. Spawn failure and capacity produce a logged failure and the default close after
accept — same as today's unreachable-ttyd path. Adding codes would mean adding client branches; the
existing set already carries the meanings.

### 5.2 `federation_terminal_ws_proxy` (`main.py:2727-2851`)

Signature gains `session: str | None = None`. **No socket code.** Three changes:

1. Append `?session=<urlencoded>` to `ws_url` when `session is not None`. Preserve the existing
   `http→ws` / `https→wss` conversion and the `else` passthrough branch.
2. Everything else — `_ws_auth_check`, `_lookup_remote_by_device_id` → `close(4004)`, the
   `CERT_NONE` SSL context, the Bearer header, `_ws_proxy_tasks` registration
   (`main.py:2792-2794`), the `FIRST_COMPLETED` + cancel relay block (`main.py:2836-2842`), and the
   `finally` — **unchanged**.
3. Docstring: state explicitly that this relay dials the remote muxplex's authenticated
   `/terminal/ws`, never a ttyd socket, and that the per-session ttyd change is therefore invisible
   here apart from forwarding `session`.

Both `_ws_proxy_tasks` registration and `FIRST_COMPLETED`-plus-cancel are preserved on both routes,
as the brief requires. Guarded by tests in §12.4.

---

## §6. API surface — all changes additive

### 6.1 New: `?session=` on two WebSocket routes

| Route | Param | Absent |
|---|---|---|
| `WS /terminal/ws` | `session` (str, optional) | Falls back to `state["terminal_session"]` — today's behavior byte-for-byte |
| `WS /federation/{device_id}/terminal/ws` | `session` (str, optional) | Not forwarded; remote falls back as above |

Additive: a client that never sends it behaves exactly as today. This is the **only** wire change
the architecture strictly requires, and it is a new optional query parameter — the most additive
change shape there is.

### 6.2 `POST /api/sessions/{name}/connect` (`main.py:1357-1448`)

Response shape **unchanged**: `{active_session, ttyd_port, sync_group, terminal_session}`.
`ttyd_port` still returns `TTYD_PORT` (7682) — §0.3.

Body, after the existing `_require_valid_session_name` + `known` membership checks:

```python
try:
    await ensure_ttyd(name)
except TtydCapacityError as exc:
    raise HTTPException(status_code=503, detail=str(exc))
except TtydSpawnError as exc:
    raise HTTPException(status_code=500, detail=f"Failed to start terminal for {name!r}: {exc}")

async with state_lock:
    state = load_state()
    group = _resolve_group_or_404(state, device_id)
    write_group_state(state, group, {"active_session": name})
    state["terminal_session"] = name
    state["terminal_group"] = group
    save_state(state)
```

Deleted:
- The 409 `terminal_conflict` gate (`main.py:1396-1405`) — §7.1.
- The same-session short-circuit (`main.py:1416-1430`) — `ensure_ttyd` is idempotent, so the
  optimization is now free and general. ~15 lines removed.
- `await kill_ttyd(); await spawn_ttyd(name)` (`main.py:1433-1434`) → one `ensure_ttyd(name)`.
  **A connect to session X no longer disturbs session Y's terminal at all.** That single line is the
  whole prize.

`takeover: bool = False` **stays in the signature** and is ignored. Removing it would 422 the
existing clients that send `&takeover=true` (`terminal.js:245`). Docstring must say: *accepted and
ignored; retained for wire compatibility; there is no longer a terminal to take over.*

New failure mode, deliberately: `/connect` can now return 500 when ttyd fails to bind. Today it
cannot, because it never verifies the spawn. Returning 200 for a terminal that does not exist — or
worse, for a ttyd that fell back to an unauthenticated TCP 7681 — is exactly the silent failure this
repo forbids. `docs/API_SEMANTICS.md` and `docs/AGENT_GUIDE.md:420` must document 500/503.

### 6.3 `DELETE /api/sessions/current` (`main.py:1581-1614`)

Response shape unchanged: `{active_session, sync_group, terminal_released}`.

```python
async with state_lock:
    state = load_state()
    group = _resolve_group_or_404(state, device_id)
    mine  = read_group_state(state, group)["active_session"]
    write_group_state(state, group, {"active_session": None})
    save_state(state)

released = False
if mine is not None and relay_count(mine) == 0:
    released = await kill_ttyd(mine)
return {"active_session": None, "sync_group": group, "terminal_released": released}
```

The `owns_terminal = state["terminal_group"] == group` branch is deleted. The `relay_count(mine) == 0`
condition replaces it and is *stronger*: it is structural, not advisory. Two devices in different
groups co-viewing session X share one ttyd; when A disconnects, B's relay is still counted, nothing
is killed, and B's terminal survives. AGENTS.md's *"closing your own private fullscreen must never
black out someone else's live terminal"* is now enforced by refcount rather than by a group claim —
and it now also holds for two devices in the **same** group, which the old check did not cover.

`terminal_released` keeps its meaning exactly: "this call tore down a terminal process."

---

## §7. The single-owner terminal guard — explicit disposition

Nothing here is left ambiguous. Each piece is deleted, kept, or redefined, with the reason.

### 7.1 `409 terminal_conflict` on `/connect` → **DELETED**

Its condition is `terminal_session is not None and terminal_group != group`
(`main.py:1396`). It exists to arbitrate a single contended resource. Post-change there is no
contended resource: A on session X and B on session Y get two ttyds and never interact. Keeping the
gate would refuse a request that now succeeds — it would be actively wrong, not merely vestigial.

Client impact: `terminal_conflict` simply stops being emitted. A client that handles it never sees
it — version-tolerant in the direction AGENTS.md requires ("the server should tolerate their
absence" has a mirror: a client tolerating a response it no longer receives). `app.js:3521` and
`terminal.js:254-267` keep their handlers as dead-but-harmless branches this change does not touch;
removing them is a separate frontend cleanup (§9.2).

### 7.2 `WS 4409` → **KEPT, REDEFINED, NARROWER**

Old meaning: *"another group holds the one terminal."* Gone with the resource.

New meaning: *"you asked to attach to a session your own group has not selected."*

```python
if device_id is not None and read_group_state(state, group)["active_session"] != target:
    await _accept_then_close(websocket, code=4409)
```

This preserves the guard's *actual stated purpose* from `docs/API_SEMANTICS.md` — *"this device is
never shown, and can never type into, a session it did not itself select"* — while dropping the
resource-arbitration meaning that no longer applies. It becomes a per-request consistency check
rather than a global-resource claim.

Note honestly what this is now worth: once a client passes `?session=`, the WebSocket *names its own
target*, so misdirection is structurally impossible for that client and 4409 fires only on genuine
desync (a stale reconnect after the user switched sessions). That is the right shape — a backstop,
not a workflow step. The residual gap is unchanged and must stay documented: **a client that sends
no `device_id` gets none of this**, and the federation relay is exactly such a client.

`_accept_then_close()` (`main.py:2404-2461`) and everything in its docstring about why closing
before `accept()` never reaches the wire is **untouched**. That mechanism is orthogonal to this
change and was hard-won.

### 7.3 `state["terminal_session"]` → **KEPT, REDEFINED**

New definition: *"the session most recently connected by the global group — the fallback target for
a WebSocket that names no session."* Still written by `/connect`, still cleared by the poll loop
when the session vanishes (`main.py:398-400`), still in `/api/state`.

It is genuinely load-bearing: it is what makes the federation relay and any no-`session` client keep
working unchanged. Its `state.py:44` comment must be rewritten — "what ttyd is actually attached to"
becomes false the moment there are N ttyds, and a stale comment asserting a dead invariant is
exactly the context poison this spec is asked to avoid.

### 7.4 `state["terminal_group"]` → **server-side logic DELETED; wire field KEPT as informational**

Every server-side branch on it goes:

| Site | Disposition |
|---|---|
| `main.py:1396` — 409 gate | Deleted with the gate (§7.1) |
| `main.py:1597` — `owns_terminal` | Replaced by the refcount check (§6.3) |
| `main.py:453-457` — pruned-group release | **Deleted entirely.** Its purpose was to stop an abandoned group holding the one terminal hostage. With no contended resource there is nothing to hold hostage, and the idle reaper (§3.10) collects the abandoned ttyd on resource grounds within 60 s. |

The field itself stays in `state.json` and `/api/state`, and `/connect` keeps writing it — one
assignment, now meaning *"the group that most recently connected `terminal_session`"*: provenance,
not a claim. Removing it would be a subtractive change to a documented public field for zero gain.

This is not the ambiguous middle the brief warns about, and the distinction is worth being precise
about: **dead server logic is deleted** (that is the context poison); **a wire field with no
consumer in this repo is retained**, and `state.py`'s docstring must state in one line that *no
server behavior branches on `terminal_group`*. A future reader must not be able to mistake it for a
live invariant.

### 7.5 Summary

| Thing | Fate |
|---|---|
| `409 terminal_conflict` | Deleted — cannot fire |
| `?takeover=true` | Accepted, ignored, documented as a no-op |
| `WS 4409` | Kept; redefined to "session you didn't select"; narrower |
| `WS 4404` | Kept; widened to cover unknown/invalid/missing session |
| `terminal_session` | Kept; redefined as the no-`session` fallback target |
| `terminal_group` | Logic deleted; field kept as informational provenance |
| `_accept_then_close` | Untouched |

---

## §8. State schema

`state.json` shape is **unchanged**. No migration, no `normalize_state()` change. Old and new muxplex
read each other's `state.json` without error, which is what makes §10's rollback cheap.

`muxplex/state.py` doc edits only (lines 43-45 and 68-72): retire "the single, server-wide ttyd"
framing; define `terminal_session` per §7.3; add the one-line "no server behavior branches on
`terminal_group`" note per §7.4.

---

## §9. Frontend

### 9.1 `terminal.js` — send the session (required)

`connectWebSocket(name, remoteId, ownDeviceId)` (`terminal.js:78-95`) appends `session` on **both**
branches:

```js
if (remoteId) {
  url = proto + '//' + location.host + '/federation/' + remoteId + '/terminal/ws'
      + '?session=' + encodeURIComponent(name);
} else {
  url = proto + '//' + location.host + '/terminal/ws?session=' + encodeURIComponent(name);
  if (ownDeviceId) url += '&device_id=' + encodeURIComponent(ownDeviceId);
}
```

Note the local branch's separator flips from `?` to `&`. `name` is the parameter already in scope
and is the session the UI believes it is showing — which is the point: the URL now *states* the
target instead of inheriting it.

### 9.2 `_showTerminalConflictOverlay` (`terminal.js:291-306`) — retext, drop Take-over

The string `"Terminal is showing another device's session"` becomes false. Replace with
`"Session changed on another device — reconnecting"`. The Take-over button no longer has anything to
take over: hide it, and have the 4409 branch re-issue `/connect` for `name` once (not a loop) before
reconnecting. `_pendingTakeover` and the `&takeover=true` append (`terminal.js:245-248`) can go;
leaving them is also safe since the server ignores the param. Keep the HTTP-409 branch
(`terminal.js:254-267`) as-is — it becomes unreachable but harmless, and `test_frontend_js.py`
asserts on source text (AGENTS.md's tripwire), so removing it is a separate, deliberate change.

### 9.3 ⛔ Do NOT remove the 800 ms settle sleep in this change

`setTimeout(_connectWebSocket, 800)` (`terminal.js:275`) is now redundant against a new server:
`/connect` returns only after readiness is *proven* (§3.6). Removing it is tempting and **wrong
right now** — a new frontend against an old server would open the WebSocket before ttyd binds,
because the old `/connect` never waited. Ship the server first; retire the sleep in a later,
separately-verified change. This is also just AGENTS.md's "API first, frontend second" applied to
the deploy order.

### 9.4 `app.js`

No required change. The `terminal_conflict` dialog at `app.js:3521, 3597-3611` becomes unreachable;
leave it (see §9.2 rationale).

---

## §10. Migration and rollback

### 10.1 Mid-upgrade, running instance

`systemctl --user restart muxplex.service` with `KillMode=process` in place (AGENTS.md — verify with
the canary, never by reading the directive back).

Old process shutdown path: lifespan cancels relays, calls the **old** `kill_ttyd()` which SIGTERMs
the PID in `~/.local/share/tmux-web/ttyd.pid` and sweeps 7682. Clean.

New process startup, in this order:
1. `validate_socket_dir()` — fail loud before anything else. A bad socket dir must abort startup, not
   surface later as a mysterious per-attach failure.
2. `reap_orphan_ttyds()` — nothing to find on a first upgrade.
3. `reap_legacy_ttyd()` — kills the old ttyd if its PID file survived (§3.9); ERROR-logs a still-live
   7682 if it didn't. **This is the case that matters:** if the old process was `SIGKILL`ed, its PID
   file is stale and a pre-upgrade ttyd — an unauthenticated writable terminal — is still listening.
   We report it loudly and give the exact command; we do not sweep the port.
4. Poll loop starts.

Users see: nothing. Sessions are untouched (a tmux session outlives every ttyd). The first
`/connect` or WS attach spawns that session's ttyd in ~20-100 ms.

### 10.2 Rollback

`state.json` is unchanged (§8), so downgrading is a plain reinstall of the previous version. Two
cleanups the operator must do, because the old code cannot know about the new artifacts:

```bash
# 1. Stop the per-session ttyds the new version left running (exact PIDs only, from OUR run files —
#    never pkill, never a port sweep).
for f in ~/.local/share/muxplex/ttyd/mx-*.json; do
  [ -e "$f" ] || continue
  pid=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["pid"])' "$f")
  ps -p "$pid" -o command= | grep -q ttyd && kill -TERM "$pid"
done

# 2. Remove the directory.
rm -rf ~/.local/share/muxplex/ttyd
```

Ship this in `CHANGELOG.md` at release time (not in the feature PR — AGENTS.md).

Left behind if skipped: orphan ttyds holding unlinked socket inodes. Unreachable (the path is gone),
so not an exposure, but they hold a tmux client each until killed. The old version's own
`kill_orphan_ttyd()` will not find them — it looks at the legacy PID path and port 7682, neither of
which the new version uses.

### 10.3 Known break, stated honestly

A third-party client that reads `ttyd_port` from `/connect` and **dials** `127.0.0.1:7682` gets
`ECONNREFUSED` after this change. That client was already violating AGENTS.md's *"All legitimate
access goes through muxplex's own authenticated `/terminal/ws` proxy"* and was already reaching a
terminal for an arbitrary session. No such client is known: `muxplex_client` parses the field and
surfaces it without dialing it (verified across `client/muxplex_client/*.py`). Call this out in the
release notes.

---

## §11. Federation implications

**No `/api/*` shape change is required, and here is the argument.**

1. `federation_terminal_ws_proxy` dials `ws(s)://<remote>/terminal/ws` — the remote muxplex's own
   authenticated endpoint, over the network, with a Bearer header. It has never touched a ttyd
   socket and does not now. UNIX sockets are process-local by construction, so the transport change
   cannot cross an instance boundary even in principle.
2. `federation_connect` (`main.py:3257-3298`) proxies `POST {remote}/api/sessions/{name}/connect`
   and returns the remote's JSON verbatim. That body's shape is unchanged (§6.2), including
   `ttyd_port`. New 500/503 outcomes flow through the existing `HTTPStatusError` → 502 mapping.
3. The only genuinely new thing is `?session=`, a **new optional query parameter** on two WS routes.
   Nothing is renamed, removed, or given new semantics. An old peer receiving `?session=X` from a new
   peer ignores the unknown param and falls back to `terminal_session` — today's behavior. A new peer
   receiving no `session` from an old peer falls back identically. Version-tolerant in both
   directions, which is precisely AGENTS.md's stated requirement.
4. The federation relay still sends no `device_id` upstream, so the remote's 4409 backstop still does
   not apply to it. **Unchanged residual gap**, already documented in `docs/API_SEMANTICS.md`; this
   change neither widens nor narrows it. It is materially less dangerous now, because with
   `?session=` forwarded the remote relays the *named* session rather than whatever its one ttyd
   happened to hold — the misdirection window closes on the transport side even though the guard
   still doesn't fire.

The genuine federation *improvement*: today, one local user connecting to a local session tears down
the ttyd a remote peer is relaying through. After this change, remote and local terminals on
different sessions are fully independent.

---

## §12. Test plan

Everything runs in the DTU (`make test`), never on a host serving a live muxplex (AGENTS.md). Commit
locally first so `git archive HEAD` tests the artifact that will be pushed.

### 12.1 Reuse the existing spike probes as the platform gate

Unchanged and re-run when qualifying a platform, a ttyd release, or a tmux release:

| Probe | Proves |
|---|---|
| `scripts/spike_ttyd_unix_socket.py` | Real RFC 6455 upgrade over `AF_UNIX`, verified from first principles |
| `scripts/spike_ttyd_relay.py` | ttyd's wire protocol end to end over the socket (AuthToken, `0x31`, `0x30`) |
| **`scripts/spike_ttyd_session_isolation.py`** | **Two ttyds, two sockets, two sessions, zero cross-talk — the load-bearing one** |
| `scripts/spike_tmux_window_size.py` | Two differently-sized clients on one session (co-viewing behavior, unchanged) |

Add one line to `scripts/README.md`: the architecture these qualified has shipped; the probes remain
the platform-qualification gate.

### 12.2 New unit tests — `muxplex/tests/test_ttyd.py` (rewrite)

| Test | Asserts |
|---|---|
| `test_socket_path_is_sock_suffixed` | Ends in `.sock` for adversarial names (unicode, 200 chars, `..`, leading `-`) |
| `test_socket_path_within_sun_path_budget` | `len(str(path)) <= 102` for all of the above |
| `test_socket_path_is_deterministic_and_distinct` | Same name → same path; 1,000 distinct names → 1,000 distinct paths |
| `test_socket_path_does_not_contain_session_name` | Name never appears in the path (hash, not concat) |
| `test_validate_rejects_drvfs_under_wsl` | Monkeypatch `is_wsl()` True + `/mnt/c/x` → `TtydSocketDirError` mentioning ENOTSUP |
| `test_validate_rejects_overlong_dir` | 90-char dir → raises, message names `MUXPLEX_TTYD_SOCKET_DIR` |
| `test_validate_rejects_group_writable_dir` | `chmod 0o770` → raises |
| `test_validate_rejects_foreign_uid_dir` | Monkeypatched `st_uid` → raises |
| `test_validate_bind_probe_leaves_no_file` | `.probe.sock` absent afterward |
| `test_spawn_argv_uses_socket_not_port` | argv is exactly `["ttyd","-W","-m","3","-i",<sock>,"tmux","attach","-t",name]`; asserts `-p` and `127.0.0.1` are **absent** |
| `test_spawn_preserves_cgroup_escape_wrap` | Port of `test_ttyd.py:148`; `should_escape()` True → argv is scope-prefixed |
| `test_spawn_preserves_tmux_env` | Port of `test_ttyd.py:119`; `env=tmux_env()` passed through |
| `test_spawn_unlinks_stale_socket_first` | Pre-existing file at the path is removed before spawn |
| `test_spawn_raises_when_socket_never_appears` | Fake live process, no file → `TtydSpawnError` whose message contains "7681" |
| `test_spawn_raises_when_process_exits_early` | `returncode=1` → raises promptly, not after the full 5 s |
| `test_socket_is_live_false_for_stale_file` | **Create a plain file at the socket path → `False`.** Guards §0.2 directly. |
| `test_socket_is_live_false_for_missing_file` | `False`, no raise |
| `test_kill_removes_socket_and_run_file` | Both unlinked even when the process needed SIGKILL |
| `test_kill_unknown_session_returns_false` | No raise |
| `test_orphan_reap_skips_pid_not_matching_ps` | Recycled PID: **no signal sent**, files removed, warning logged |
| `test_orphan_reap_kills_confirmed_ttyd` | `ps` shows `ttyd ... -i <sock>` → SIGTERM sent |
| `test_orphan_reap_removes_socket_without_run_file` | Bare `.sock` cleaned up |
| `test_legacy_reap_never_sweeps_port` | Assert `lsof` is never invoked and no `os.kill` on an unconfirmed PID; a live 7682 produces an ERROR log |
| `test_no_lsof_anywhere_in_module` | Source-level: `"lsof"` does not appear in `ttyd.py`. Guards the deleted sweep from returning. |
| `test_ensure_is_idempotent` | Second call with a live socket spawns nothing |
| `test_cap_reaps_idle_then_spawns` | `MAX_TTYDS=2`; third ensure reaps the idle one |
| `test_cap_raises_when_all_busy` | `MAX_TTYDS=2`, both `relays=1` → `TtydCapacityError` |
| `test_idle_reaper_spares_active_relays` | `relays=1` and 10× the idle timeout → survives |
| `test_ttyd_port_constant_still_7682` | Guards the wire contract against a "remove unused constant" cleanup |

### 12.3 New unit tests — `muxplex/tests/test_ws_proxy.py` (extend)

Existing `FakeTtydWs` and the `_patch_ttyd_auto_spawn` fixture adapt: patch `unix_connect` instead of
`websockets.connect`, and `socket_is_live` instead of `_ttyd_is_listening`.

| Test | Asserts |
|---|---|
| `test_ws_dials_session_specific_socket` | `?session=X` → `unix_connect` called with `socket_path_for("X")` |
| `test_ws_falls_back_to_terminal_session` | No `session` → dials `socket_path_for(state["terminal_session"])` |
| `test_ws_unknown_session_closes_4404` | `?session=nope` → 4404, no `unix_connect` call |
| `test_ws_invalid_session_name_closes_4404` | `?session=-bad;rm` → 4404 |
| `test_ws_no_target_at_all_closes_4404` | No param, `terminal_session=None` → 4404 |
| `test_ws_session_not_selected_by_group_closes_4409` | `device_id` set, group's `active_session != session` → 4409 |
| `test_ws_registers_and_discards_relay_task` | `_ws_proxy_tasks` gains then loses the task — **guards a brief-named property** |
| `test_federation_ws_registers_relay_task` | Same for the federation route — **guards the other** |
| `test_relay_uses_first_completed_not_gather` | Close the client side; handler returns while the fake ttyd is still live — **guards the shutdown-hang fix on both routes** |
| `test_relay_refcount_released_on_disconnect` | `relay_count(X) == 0` in `finally`, including on an exception |
| `test_federation_forwards_session_param` | Upstream URL contains `?session=X`, correctly urlencoded |
| `test_federation_omits_session_when_absent` | No `session` in the upstream URL |
| `test_client_disconnect_during_spawn_skips_accept` | Port of `test_ws_proxy.py:585` onto `_prepare_ttyd` |
| `test_spawn_failure_does_not_hang_client` | `_prepare_ttyd` raising → handler returns, no `accept()` |

### 12.4 New unit tests — `muxplex/tests/test_api.py` / `test_sync_groups_api.py` (edit)

| Test | Asserts |
|---|---|
| `test_connect_returns_ttyd_port_7682` | `test_api.py:998, 1078` **keep passing unchanged** — the client contract |
| `test_connect_two_sessions_leaves_both_ttyds_alive` | Connect X then Y → both sockets live. **The core behavioral claim.** |
| `test_connect_no_longer_returns_terminal_conflict` | Two groups, two sessions → both 200; no 409 |
| `test_connect_accepts_and_ignores_takeover` | `?takeover=true` → 200, no 422 |
| `test_connect_500_on_spawn_failure` | `ensure_ttyd` raising `TtydSpawnError` → 500 |
| `test_connect_503_on_capacity` | `TtydCapacityError` → 503 |
| `test_delete_current_kills_only_own_session` | A on X, B on Y; A deletes → Y's ttyd untouched |
| `test_delete_current_spares_coviewed_session` | Two relays on X; one deletes → ttyd survives, `terminal_released is False` |
| `test_client_contract_unchanged` | Extend `test_client_contract.py`: `parse_connect_result` succeeds against the live response |

### 12.5 Integration — real tmux, real ttyd, real sockets (`test_integration.py`, `@pytest.mark.integration`)

`test_two_simultaneous_independent_terminals` — **the acceptance test for this entire change.** It
must run against real binaries through the real ASGI app, because the failure this change fixes is a
process-lifecycle failure that no mock reproduces.

1. Real tmux server on an isolated `tmux -L mxtest-<uuid>` socket + isolated `TMUX_TMPDIR`, `env -u TMUX`.
2. Create sessions `mxtest-a` and `mxtest-b`.
3. `POST /api/sessions/mxtest-a/connect?device_id=devA`, then `.../mxtest-b/connect?device_id=devB`
   (devB in its own sync group). **Assert both return 200 — no 409.**
4. Assert both socket files exist, both `socket_is_live`, and the two paths differ.
5. Open **both** WebSockets concurrently via `TestClient`, each with its own `?session=` and `device_id`.
6. On each: send the `{"AuthToken": ""}` text frame, then a `0x31` resize, then type a
   session-unique marker via `0x30` + `Enter`.
7. Drain each socket for ≤5 s. **Assert A's output contains A's marker and never B's, and vice
   versa.** This is `spike_ttyd_session_isolation.py`'s claim, re-proven through muxplex's own
   authenticated proxy rather than against raw ttyd.
8. Close A's WebSocket. Assert B's socket is still live and B still echoes — **A's teardown does not
   disturb B.** This is the exact failure mode of today's architecture.
9. Teardown: kill only captured PIDs; `tmux -L mxtest-<uuid> kill-server`; unlink the tmux socket
   file (`scripts/spike_ttyd_harness.py` does this — dead sockets otherwise accumulate). Never
   `pkill`, never a bare `kill-server`, never touch 7682 or 8088.

Supporting integration tests:

| Test | Asserts |
|---|---|
| `test_ttyd_survives_second_connect_to_same_session` | Same-session reconnect does not churn the PTY (replaces the deleted short-circuit's guarantee) |
| `test_orphan_reap_kills_real_ttyd_across_restart` | Spawn real ttyd, drop the registry, `reap_orphan_ttyds()` → process gone, socket gone |
| `test_idle_reaper_kills_real_idle_ttyd` | `IDLE_REAP_SECONDS=0.5`, no relay → reaped; socket gone; **tmux session still alive** |
| `test_sigterm_removes_socket_file` | Confirms the spike finding against the shipped spawn path |
| `test_stale_socket_does_not_block_rebind` | Leave a stale `.sock`, spawn → succeeds |

### 12.6 Shutdown — `test_shutdown.py` (extend)

`test_lifespan_kills_all_ttyds`: three registered ttyds → all three killed, all sockets and run files
gone, within the existing 3 s `wait_for` budget. `kill_all_ttyd()` gathers, so wall time stays ~one
SIGTERM round trip, preserving AGENTS.md's ~0.5 s shutdown target.

### 12.7 Frontend

`node --test frontend/tests/*.mjs` (the glob — AGENTS.md). Add to `test_terminal.mjs`:
`session=` present and urlencoded on both the local and federation URL branches; `&device_id=`
follows `?session=` on the local branch. `test_shared_scope.mjs` covers any new top-level binding
automatically.

### 12.8 Evidence required before the work is called done

1. `make test` green in the DTU, including `-m integration`.
2. `test_two_simultaneous_independent_terminals` passing, with the captured marker output pasted
   into the PR — **this is the proof that the conflict is gone.**
3. All four `scripts/spike_*.py` exit 0 on the DTU platform.
4. `node --test frontend/tests/*.mjs` green.
5. `test-latest-deps` green (proves the `websockets>=14.0` floor against a fresh resolve).
6. Manual DTU check: two browsers, two sessions, simultaneous typing, no cross-talk, and closing one
   tab leaves the other live.
7. `ls ~/.local/share/muxplex/ttyd/` after the run shows mode `0700` and no leftover `.sock`/`.json`.

---

## §13. Files changed

| File | Change |
|---|---|
| `muxplex/ttyd.py` | Rewrite. Net **negative** LOC: `_kill_pids_on_port`, both port sweeps, `TTYD_BIND_ADDRESS` out; socket derivation, registry, reapers in. |
| `muxplex/main.py` | `connect_session`, `delete_current_session`, `terminal_ws_proxy`, `federation_terminal_ws_proxy`, `_prepare_ttyd`, lifespan, poll cycle. `_ttyd_is_listening` deleted. |
| `muxplex/state.py` | Docstring only (§8). |
| `muxplex/frontend/terminal.js` | `?session=` on both branches; conflict-overlay retext. |
| `pyproject.toml` | `websockets>=14.0` (§0.4). |
| `AGENTS.md` | Rewrite "ttyd is loopback-only by design" for `AF_UNIX`; delete the `TTYD_PORT` hazard from "Running a second instance" (per-instance socket dirs supersede it); keep the `0.0.0.0` incident record. |
| `docs/API_SEMANTICS.md` | Rewrite "single shared ttyd process": no longer single; `terminal_conflict` retired; 4409 redefined; `?session=` documented; `terminal_group` marked informational. Keep the whole 4409-never-reached-the-wire incident verbatim — it is about `_accept_then_close`, which is untouched. |
| `docs/AGENT_GUIDE.md` | `/connect` can now return 500/503 (line ~420). |
| `scripts/README.md` | One line: architecture shipped; probes remain the platform gate. |
| `muxplex/tests/test_ttyd.py` | Rewrite (§12.2). |
| `muxplex/tests/test_ws_proxy.py` | Extend (§12.3). |
| `muxplex/tests/test_api.py`, `test_sync_groups_api.py`, `test_integration.py`, `test_shutdown.py`, `test_client_contract.py` | Extend (§12.4-12.6). |
| `muxplex/frontend/tests/test_terminal.mjs` | Extend (§12.7). |
| `docs/plans/2026-08-01-per-device-sync-groups-plan.md` | Append a one-paragraph note that §0's blocking finding is now retired, pointing here. Do not rewrite the record. |

**Not changed:** `sessions.py`, `cgroup_escape.py`, `settings.py`, `terminal_input.py`, `auth.py`,
`tls.py`, `views.py`, `bells.py`, `service.py`, `client/`, `CHANGELOG.md` (release-time, owner).

---

## §14. Success criteria

1. Two devices attach to two different sessions simultaneously. Both work. Neither disturbs the
   other. **No 409, no 4409, no reconnect loop.** Proven by §12.5 with captured output.
2. No TCP port is bound by any ttyd. `ss -ltnp` / `lsof -i :7682` show nothing owned by muxplex.
3. `lsof` appears nowhere in `muxplex/ttyd.py` (asserted by test).
4. A socket path that does not end in `.sock` is unconstructible: `socket_path_for()` cannot produce
   one, and the readiness gate would fail loudly if it somehow did.
5. A DrvFs socket dir aborts startup with a legible error naming ENOTSUP — never a live process
   spinning at 10 ms forever.
6. Every socket path is ≤102 bytes for every session name, asserted over adversarial inputs.
7. Proof of bind is the socket file's existence after a deliberate unlink — never exit code, never
   liveness, never ttyd's log. Proof of *liveness* is a real `connect()` — never `exists()`.
8. `POST /connect` still returns an int `ttyd_port`; `muxplex_client.parse_connect_result` still
   parses the live response (asserted by `test_client_contract.py`).
9. A ttyd that fails to bind produces a 500 with the reason. Nothing returns 200 for a terminal that
   does not exist.
10. Both WS relays register in `_ws_proxy_tasks` and both use
    `asyncio.wait(FIRST_COMPLETED)` + cancel-the-other. Asserted, not assumed.
11. Killing a ttyd never kills a tmux session. Asserted in §12.5.
12. Startup reaps orphans without ever signalling a PID it has not positively identified as our ttyd.
13. Steady-state process count after browsing 51 sessions and walking away: ≤ (open terminals), not 51.

---

## §15. Deliberately not done

| Rejected | Why |
|---|---|
| Keep a TCP port per session | Reintroduces allocation, sweeps, and off-box exposure — the three things this deletes. |
| `Path.exists()` as the liveness probe | Returns `True` for a stale socket from a `SIGKILL`ed ttyd (§0.2). |
| A `ttyd_socket_dir` **setting** | Would have to join `LOCAL_ONLY_KEYS`; an env var sidesteps the fence question entirely and cannot be reached by a Bearer-key holder (§3.2). |
| `ttyd_socket_dir` on `/api/instance-info` | Unauthenticated endpoint; nothing outside this process needs it; it locates a directory of unauthenticated writable terminals (§3.3). |
| Killing an unidentified PID on port 7682 during migration | That *is* the dangerous sweep. Detect and report loudly instead (§3.9). |
| New WebSocket close codes for spawn failure / capacity | Existing 4404/4409 carry the meanings; new codes mean new client branches for paths a user should never see (§5.1). |
| Removing `terminal_session` | It is the fallback target that keeps the federation relay and every no-`session` client working unchanged (§7.3). |
| Removing `terminal_group` from the wire | Subtractive change to a documented public field for zero gain. The **logic** is deleted; the field is retained and explicitly marked informational (§7.4). |
| Removing the frontend's 800 ms sleep now | New client + old server would open the WS before ttyd binds. Server first (§9.3). |
| Making `/connect` a pure state write (no spawn) | Silently changes an existing endpoint's guarantee that the terminal is ready on return. Keeping the spawn is both smaller and more honest (§6.2). |
| `pkill`/`killall`/name-matching anywhere | AGENTS.md, and this repo's incident history. Every kill in this spec targets a PID we recorded and positively identified. |

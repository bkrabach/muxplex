# muxplex-client

Typed sync/async HTTP client for the [muxplex](https://github.com/bkrabach/muxplex)
tmux-session-dashboard API.

Ships as its own PyPI distribution (import name `muxplex_client`) so a
consumer that only needs to make ~12 HTTP calls -- a Stream Deck sidecar, an
Amplifier tool module -- never has to install the server's dependencies
(fastapi, uvicorn, python-pam, ...) or its `muxplex` server console script.
See [`../muxplex-client-design.md`](../muxplex-client-design.md) for the full
design rationale.

## Install

```bash
pip install muxplex-client
```

Runtime dependency: `httpx>=0.27.0`. Nothing else -- no `muxplex` server
dependency, and so no `muxplex` server console script on a client machine.

That installs the **library**. The distribution also declares a `muxplex-client`
console script, but it is new and is in no published release yet -- to get the
CLI on `PATH` today, see [CLI](#cli) below.

## Usage

Synchronous:

```python
from muxplex_client import MuxplexClient

with MuxplexClient("https://your-server:8088", "federation-key") as client:
    for session in client.sessions():
        if session.bell.needs_attention:
            print(f"{session.name} needs attention")
```

Asynchronous (Amplifier tool modules, or any asyncio caller):

```python
from muxplex_client import AsyncMuxplexClient

async with AsyncMuxplexClient("https://your-server:8088", "federation-key") as client:
    result = await client.run_shell_command("my-session", "pytest -q")
    print(result.exit_code, result.elapsed)
```

A localhost caller needs no credential -- `federation_key=None` is the
default and is fine when running on the same host as the server.

## CLI

A `muxplex-client` console script exposes every feature of the muxplex HTTP API
as a command, so an AI agent (or a shell script) can drive a muxplex server
without writing Python.

That script is **new**. It is not in 0.30.1 or any earlier release, so
`uv tool install muxplex-client` against today's PyPI fails with
`No executables are provided by package muxplex-client` -- the published
distribution ships the library only. Three forms, each true in a different
state:

| Form | Command | True when |
|---|---|---|
| From a checkout of this repo | `uv run --directory client muxplex-client ...` | **Works right now.** No install; run it from the repo root. |
| From the repo on GitHub | `uv tool install "git+https://github.com/bkrabach/muxplex@main#subdirectory=client"` | Once the CLI lands on `main`. Puts the `muxplex-client` executable on `PATH`. |
| From PyPI | `uv tool install muxplex-client` | Once a release newer than 0.30.1 is published. |

Prefer `uv tool install` for an agent: it puts the executable on `PATH` in its
own isolated environment. `pip install muxplex-client` installs the same script
into whichever environment is active -- fine for a library consumer, and equally
subject to the release status above.

The CLI is a thin shell over the library above: it does argparse wiring,
output rendering, and exit codes -- config resolution, CAS retry, and TLS
remediation all live in (and are fully tested at) the library layer, so this
section only documents the command surface.

### Global flags

All default to picking up the environment/disk-discovered value (see
"Configuration resolution" below) when omitted; pass them before the
subcommand.

| Flag | Meaning | Env var |
|---|---|---|
| `--url URL` | Override server URL | `MUXPLEX_URL` |
| `--key KEY` | Federation key literal (prefer `--key-file`; a literal here lands in shell history) | `MUXPLEX_KEY` |
| `--key-file PATH` | Read the federation key from a file | `MUXPLEX_FEDERATION_KEY_FILE` |
| `--ca PATH` | CA certificate path | `MUXPLEX_CA_FILE` |
| `--timeout SECONDS` | HTTP timeout | `MUXPLEX_TIMEOUT` |
| `--json` | Emit JSON to stdout instead of human-readable text | -- |

### Configuration resolution

Every value above resolves **explicit flag > environment variable >
discovered on disk > built-in default**. `muxplex-client info --verbose`
prints which tier won for each field -- read it first when a connection
fails for a non-obvious reason. The default server URL is
`http://127.0.0.1:8088`, unless a local CA certificate is discovered at
`~/.config/muxplex/ca/muxplex-ca.crt`, in which case it defaults to
`https://127.0.0.1:8088`. The federation key, if not given explicitly or via
`MUXPLEX_KEY`, is read from `~/.config/muxplex/federation_key` if present;
`None` (no credential) is a valid, working default for a localhost server.

### Commands

```
info [--verbose]              Show instance info (--verbose also prints config sources)
health                        Unauthenticated liveness check
auth-mode                     Show the server's auth mode and running username
ca                            Print the local CA certificate PEM to stdout, and nothing else

ls [--sort attention]         List sessions (cheap, server-resolved view)
sessions                      List sessions including pane snapshots (expensive)
show NAME [--lines N]         Show one session's current pane content
new NAME [--no-wait] [--wait-timeout S]
                               Create a new tmux session
rm NAME [--yes]                Delete a session (confirmation gate)
disconnect                    Disconnect the current ttyd session
connect NAME                  Connect to a session -- WARNING: moves the human's browser view too

send NAME [--text T] [--key K]... [--enter] [--lines N]
                               Type into a session; always prints the read-back snapshot
run NAME COMMAND [--timeout S] [--lines N] [--no-bell] [--exit-expr E]
                               Run a shell command to completion; exits with the REMOTE
                               command's exit code (see "Exit codes" below)

bell ring NAME                Record a bell fire for a session
bell clear NAME                Acknowledge a session's bell

state                         Show server state
state set [--active-view V] [--active-session S] [--active-remote-id R] [--clear-active-session]
                               Patch server state -- WARNING: server-global, last-writer-wins
settings                      Show current settings
settings set KEY JSON_VALUE [--allow-destructive]
                               Set one top-level settings key (safe CAS read-modify-write)
settings sync                 Show syncable settings and their timestamps
settings push FILE|-           Push a JSON settings-sync payload

fed ls                        List federation sessions
fed connect DEVICE SESSION     Connect to a session on a remote device
fed new DEVICE NAME            Create a session on a remote device
fed rm DEVICE NAME [--yes]     Delete a session on a remote device (confirmation gate)
fed bell-clear DEVICE NAME     Clear a bell on a remote device's session
fed generate-key [--yes]       Rotate this server's federation key -- invalidates every
                               existing client (confirmation gate)

heartbeat [--device-id ID]     Register or update this device's heartbeat
setup-hooks                   Re-register tmux hooks (call after a tmux server restart)
```

`settings set KEY JSON_VALUE` parses the value as JSON, falling back to a
bare string when JSON parsing fails. Dotted keys are not supported --
top-level only.

### Exit codes

Every client-side failure exits `1` -- there is no multi-code convention in
this CLI. The one deliberate exception is **`run`, which exits with the
REMOTE command's exit code** on success (that's the whole point of `run`, so
`muxplex-client run build "pytest" && deploy` behaves as expected). A
client-side failure in `run` itself (unreachable, forbidden, timeout) still
exits `1`.

### Safety gates

- `rm`, `fed rm`, and `fed generate-key` prompt for confirmation unless
  `--yes` is given. Non-TTY stdin without `--yes` is a hard error, never an
  implicit yes.
- `send` never prompts (typing input is the tool's purpose -- the server's
  `input_enabled`/`input_allowed_sessions` fences are the real control), but
  it always prints the read-back snapshot so a caller never fires blind.
- Passing `--key` on the command line prints a warning to stderr (it lands
  in shell history); prefer `--key-file`.
- A `403` from `send`/`run` prints the literal JSON to add to
  `~/.config/muxplex/settings.json` and never suggests a retry -- there is
  no API path around that fence, by design (see `docs/AGENT_GUIDE.md` §7).

### Examples

```bash
# See what's running
muxplex-client ls

# Create a session and run a command in it, failing the shell on non-zero
muxplex-client new build
muxplex-client run build "pytest -x" && echo "tests passed"

# Type into a session and see the result immediately
muxplex-client send build --text "ls -la" --enter

# Point at a remote server with TLS, machine-readable output
muxplex-client --url https://my-host:8088 --ca ~/.config/muxplex/ca/muxplex-ca.crt \
    --json sessions

# Debug a connection problem
muxplex-client info --verbose
```

## What's in here vs. what isn't

See `muxplex-client-design.md` §3 for the full included/excluded endpoint
table and rationale. Notably excluded: `PATCH /api/settings` (highest
blast-radius operation in the API; a v2 concern requiring CAS + 409 retry +
backstop discrimination), all federation endpoints (server-to-server
protocol, no client consumer), and `/api/internal/setup-hooks` (internal,
self-healing as of server v0.18.0).

## Version alignment

`muxplex_client.__version__` is cut in lockstep with the muxplex server
version -- one `vX.Y.Z` tag publishes both wheels. This is provenance
("cut against server X"), not a runtime requirement: the client declares no
dependency on the `muxplex` package and enforces no version at runtime.
`MIN_SERVER_VERSION` backs an opt-in `check_server()` helper the caller may
call; it is never invoked automatically.

The load-bearing correctness mechanism is
`muxplex/tests/test_client_contract.py`, living in the **server's** own test
suite: it drives this client over `httpx.ASGITransport` against the real
FastAPI app and asserts every field the client parses actually exists on the
real response, that mirrored constants (`KNOWN_KEYS`, `MAX_CAPTURE_LINES`,
`DEFAULT_CAPTURE_LINES`) equal their server originals, and that
`Bell.needs_attention` agrees with the server's own predicate across a truth
table.

## The shell-command sentinel

`run_shell_command()` (and the lower-level `muxplex_client.sentinel` module)
implements the completion-detection convention from `AGENT_GUIDE.md` §6.2/§6.4:
wrap a command with `; rc=$?; ... ; echo "MUXPLEX_DONE_<token>_EXIT_$rc"` and
poll the pane for the marker.

**This assumes the target pane is an idle POSIX shell prompt.** It is *not* a
general HTTP contract -- it fails (types a garbage line into whatever is
actually running, then hangs until timeout) against vim, a REPL, `less`, a
TUI, an ssh session, or a non-POSIX shell without a matching `exit_expr`.

The one correctness rule worth calling out explicitly: matching must be
**digit-anchored** (`MUXPLEX_DONE_<token>_EXIT_(\d+)`), never a bare-token
substring check. tmux echoes the literal, unexpanded `...EXIT_$?` into the
pane the instant you send the line -- before the shell has even run it -- so
a bare-token match reports "done" with a bogus exit code immediately. See
`muxplex_client/sentinel.py`'s docstring and `tests/test_sentinel.py`'s
digit-anchor regression test.

## Development

```bash
uv sync --extra dev   # from this directory, or from the repo root via the
                       # [tool.uv.workspace] declaration
uv run pytest
```

`tests/` is pure -- no network, no server import, and passes with `muxplex`
not installed at all.

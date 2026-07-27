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
dependency, no console script installed.

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

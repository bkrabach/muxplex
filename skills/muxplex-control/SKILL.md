---
name: muxplex-control
description: >
  Use when driving a running muxplex server from outside with the muxplex-client
  CLI — list tmux sessions, read a pane's current output, type into a live shell,
  answer a prompt that is waiting, send C-c, create or delete a session, or run a
  command in a session and get its real exit code back. Triggers on muxplex,
  muxplex-client, tmux session dashboard, read pane output, send keys to a
  session, type into a terminal, run a command in a tmux session, session bell.
  Carries the safety rules that make this survivable — the default-closed input
  fence and what a 403 means, read-before-you-type, run-vs-send, the ~2s read
  cache, the 30-line output cap, and which commands move the human's own screen.
  Load it before issuing muxplex commands rather than improvising against --help.
license: MIT
version: 1.0.0
user-invocable: true
---

# Driving muxplex with `muxplex-client`

muxplex is a dashboard over a live tmux server; `muxplex-client` is the CLI that exposes its whole HTTP API without writing Python. Reach for it when the terminal you need is **not the one you are running in** — a build someone started in another window, a deploy sitting on a `[y/N]` prompt, a session you want to create, drive to completion, and tear down. Everything you do here lands in a real pane on a real machine, usually one a human is also looking at, so this is closer to operating someone else's keyboard than to running a subprocess.

**Getting the CLI.** The `muxplex-client` console script is new — it is not in 0.30.1 or any earlier release, so `uv tool install muxplex-client` fails today with `No executables are provided by package muxplex-client`. From a checkout of this repo, `uv run --directory client muxplex-client …` works right now. Once the CLI lands on `main`, `uv tool install "git+https://github.com/bkrabach/muxplex@main#subdirectory=client"` puts it on `PATH`. All three forms and when each becomes true are in `cli-reference.md`.

## At a glance

Global flags go **before** the subcommand: `--url`, `--key`, `--key-file`, `--ca`, `--timeout`, `--json`.

| Command | What it does |
|---|---|
| `muxplex-client info [--verbose]` | Instance name, version, tmux socket dir, bell-hook state. `--verbose` shows which config tier won — the first thing to run when a connection fails. |
| `muxplex-client ls [--sort attention]` | Cheap session list, resolved server-side (view membership, needs-attention, ordering). Start here. |
| `muxplex-client show NAME [--lines N]` | One session's current pane content. **30 lines unless you ask for more** (max 2000). |
| `muxplex-client sessions` | Every session *with* pane snapshots. Expensive; prefer `ls` + `show`. |
| `muxplex-client new NAME` | Create a tmux session, then poll until it is actually visible. |
| `muxplex-client rm NAME --yes` | **Kills the tmux session.** `--yes` is mandatory when stdin is not a TTY. |
| `muxplex-client send NAME [--text T] [--key K]… [--enter] [--lines N]` | Type into a live pane. Always prints the read-back snapshot. |
| `muxplex-client run NAME "COMMAND"` | Run a command to completion and **exit with its real exit code**. Use this, not `send`, for anything long. |
| `muxplex-client bell ring\|clear NAME` | Raise or acknowledge the human-facing attention signal. |
| `muxplex-client connect NAME` | **Moves the human's browser view.** Not needed to read or type. |

Everything else — `health`, `auth-mode`, `ca`, `disconnect`, `state`, `settings`, `fed`, `heartbeat`, `setup-hooks` — plus every flag, default, bound, and exit code:

```
read_file("${SKILL_DIR}/cli-reference.md")
```

## Worked examples

Orient before touching anything:

```bash
muxplex-client --json ls
muxplex-client --json show muxplex-control --lines 200
```

Run something long and branch on the outcome. `run` exits with the *remote* command's exit code, so ordinary shell logic works:

```bash
muxplex-client run build "pytest -x" && echo "tests passed"
muxplex-client --json run build "make test" --lines 1000 --timeout 1800
```

Answer a prompt that is waiting — read first, then type:

```bash
muxplex-client show deploy --lines 50
muxplex-client send deploy --text "y" --enter
```

Interrupt a runaway process (a key with no text):

```bash
muxplex-client send build --key C-c
```

Drive a menu, then confirm. Keys are sent in the order given, after any `--text`:

```bash
muxplex-client send installer --key Down --key Down --key Enter
```

Create a scratch session, use it, destroy it:

```bash
muxplex-client new agent-scratch
muxplex-client run agent-scratch "uname -a"
muxplex-client rm agent-scratch --yes
```

Point at a remote instance over TLS, machine-readable:

```bash
muxplex-client --url https://my-host:8088 \
    --key-file ~/.config/muxplex/federation_key \
    --ca ~/.config/muxplex/ca/muxplex-ca.crt \
    --json ls
```

When something will not connect, ask the CLI where its config came from before guessing:

```bash
muxplex-client info --verbose
```

## Rules an agent must follow

> **1. Localhost needs no credential. The LAN IP is not localhost.**
> The server's auth bypass is a literal match on `127.0.0.1` / `::1` at the socket level. On the same machine, `muxplex-client info` just works with no key and auto-discovers the local CA. Reach the *same server* over its LAN address (`https://192.168.x.x:8088`) and you are a remote client: pass `--key-file ~/.config/muxplex/federation_key`. "It worked yesterday from the same laptop" is usually this.

> **2. Pass `--json`.**
> The human renderer is for humans. Almost every agent invocation should use `--json` and parse the result rather than scraping padded columns and `---` banners.

> **3. Read before you type. Never fire input blind.**
> `send` always prints the read-back snapshot for exactly this reason — use it. Confirm the prompt moved, the command echoed, or the expected output appeared before sending the next thing. The classic failure is typing the second command into a prompt still busy with the first; both lines end up mangled into one, and nothing errors.

> **4. A 403 from `send` or `run` is a full stop, not an obstacle.**
> `input_enabled` and `input_allowed_sessions` default closed and are **local-file-only** — they live in `~/.config/muxplex/settings.json` on the server and cannot be changed through the API. `PATCH /api/settings` *appears to succeed with 200 while silently ignoring them*, so a "did it work?" check will lie to you. On a 403 the CLI prints the exact JSON to add; relay that to the human and stop. There is no workaround, and hunting for one means something has already gone wrong.

> **5. Use `run`, not `send`, for anything long-running.**
> `run` wraps the command in a completion sentinel and returns the real exit code; `muxplex-client run` then exits with that same code. `send` cannot tell you when a command finished — **a silent pane and a finished pane look identical**, and there is no exit code, no idle flag, no `pane_dead` to distinguish them. `run` assumes the target is an idle POSIX shell prompt: against vim, `less`, a REPL, an ssh session, or a TUI it types a garbage line and hangs until timeout.

> **6. A just-created session may 404 for a moment. That is the cache.**
> Read endpoints serve a ~2 second poll cache, so a session that was created successfully is briefly invisible. `new` already polls for you (`--wait-timeout`, default 6s). A 404 right after a create is not a failed create — do not retry the create.

> **7. `connect` and `state set` move the human's screen.**
> There is one active session and one active view **per server**, shared with the browser tab, any sidecar, and the person sitting in front of it — last writer wins. An agent that only reads panes and types into them never needs either command. Leave them alone unless moving the human's view is the actual intent.

> **8. `rm` is real deletion.**
> It kills the tmux session and everything running in it. `--yes` is required in non-interactive use *deliberately* — the confirmation gate is the feature. Never blanket-`rm` sessions you did not create; the list includes the human's own working sessions.

> **9. Output is capped at 30 lines by default.**
> `show` and `send`'s read-back both default to 30 lines. Anything with real output — a test run, a build, a long `git log` — needs `--lines N` (max 2000) or the earlier lines are silently gone. Out-of-range values are rejected with a 400 rather than quietly clamped, so ask for what you need.

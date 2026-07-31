# `muxplex-client` full command reference

Companion to `SKILL.md`. The authoritative surface is `client/muxplex_client/cli.py`
in this repo — when this file and `muxplex-client <cmd> --help` disagree, `--help`
is right and this file has a bug.

---

## Getting `muxplex-client`

The `muxplex-client` console script is **new**. It does not exist in 0.30.1 or any
earlier release, so `uv tool install muxplex-client` against today's PyPI fails with
`No executables are provided by package muxplex-client` — the published distribution
ships the library only. Three forms, each true in a different state:

| Form | Command | True when |
|---|---|---|
| From a checkout of this repo | `uv run --directory client muxplex-client …` | **Works right now.** No install; run it from the repo root. |
| From the repo on GitHub | `uv tool install "git+https://github.com/bkrabach/muxplex@main#subdirectory=client"` | Once the CLI lands on `main`. Puts the `muxplex-client` executable on `PATH`. |
| From PyPI | `uv tool install muxplex-client` | Once a release newer than 0.30.1 is published. |

---

## Global flags

Pass these **before** the subcommand. Each resolves
**explicit flag > environment variable > discovered on disk > built-in default**.

| Flag | Meaning | Env var |
|---|---|---|
| `--url URL` | Server URL | `MUXPLEX_URL` |
| `--key KEY` | Federation key literal — prints a warning, since it lands in shell history | `MUXPLEX_KEY` |
| `--key-file PATH` | Read the federation key from a file (preferred) | `MUXPLEX_FEDERATION_KEY_FILE` |
| `--ca PATH` | CA certificate path | `MUXPLEX_CA_FILE` |
| `--timeout SECONDS` | HTTP timeout | `MUXPLEX_TIMEOUT` |
| `--json` | Emit JSON to stdout instead of human-readable text | — |

Defaults worth knowing:

- Server URL defaults to `http://127.0.0.1:8088`, or `https://127.0.0.1:8088` when
  a local CA is discovered at `~/.config/muxplex/ca/muxplex-ca.crt`.
- The federation key, if not given, is read from `~/.config/muxplex/federation_key`
  when that file exists. `None` (no credential) is valid and working for a
  localhost server — the server's auth bypass is a socket-level match on
  `127.0.0.1` / `::1`, so it does **not** apply to the machine's LAN address.
- `muxplex-client info --verbose` prints which tier won for each field. Run it
  first when a connection fails for a non-obvious reason.

---

## Instance and connectivity

| Command | Notes |
|---|---|
| `info [--verbose]` | Instance name, `device_id`, `version`, `federation_enabled`, `tmux_socket_dir`, `bell_hook_armed`. `--verbose` appends `config_sources`. |
| `health` | Unauthenticated liveness check. |
| `auth-mode` | The server's auth mode and running username. |
| `ca` | Prints the local CA certificate PEM to stdout and nothing else — ignores `--json`. Pipe it to a file to install trust on a remote client. |

`tmux_socket_dir` matters if you ever create tmux sessions *directly* instead of
through this CLI: muxplex only sees sessions on its own tmux server, and a
session created under a different `TMUX_TMPDIR` is silently invisible — no error,
it simply never appears.

---

## Reading sessions

| Command | Notes |
|---|---|
| `ls [--sort attention]` | Cheap. Server-resolved view membership, needs-attention predicate, and ordering. `--sort attention` puts bells first, then the active session, then recency. Only `attention` is accepted. |
| `sessions` | Every session **with** pane snapshots. Expensive — served from a shared ~2s cache that the PWA and any sidecar also consume. Prefer `ls` + `show`. |
| `show NAME [--lines N]` | Single-session live capture. |

`--lines` bounds (identical on `show` and `send`):

| Value | Result |
|---|---|
| omitted | 30 lines (`DEFAULT_CAPTURE_LINES`) |
| `0` | **400** — `lines must be between 1 and 2000 (got 0)` |
| `2000` | accepted — the exact ceiling |
| `2001` | **400** |

Out-of-range is always rejected, **never silently clamped**. Sessions get their
tmux `history-limit` raised to 5000 on creation so a max-depth request has real
scrollback behind it.

---

## Session lifecycle

| Command | Notes |
|---|---|
| `new NAME [--no-wait] [--wait-timeout S]` | Creates the session, then polls the ~2s read cache until it is visible. `--wait-timeout` defaults to `6.0` — this is the wait-for-visibility ceiling, **not** the HTTP timeout. The create POST itself always gets a 30s floor regardless of the global `--timeout`, because the server runs the operator's `new_session_template` synchronously first. |
| `rm NAME [--yes]` | **Kills the tmux session.** Prompts unless `--yes`; non-TTY stdin without `--yes` is a hard error, never an implicit yes. |
| `disconnect` | Disconnects the current ttyd session. Kills nothing. |
| `connect NAME` | Points the web terminal at a session. **`active_session` is server-global, last-writer-wins — this moves the human's browser view.** Not required in order to read or type. |

Session names must match `^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$` — ASCII letters,
digits, `_ . -`, first character alphanumeric-or-underscore, 1–64 chars. No
whitespace, no `:`, no leading `-` or `.`. Anything else is a 400 at the boundary.

---

## Typing into a session

### `send NAME [--text T] [--key K]… [--enter] [--lines N]`

Sends in a fixed order: **`--text`, then each `--key` in the order given, then
`--enter`**. At least one of the three is required or the CLI exits 1 without
calling the server.

- `--text` — typed literally, max 8192 bytes UTF-8 (413 above that). Delivered via
  `tmux send-keys -l` through argv, never a shell — so muxplex itself never
  interprets your text. That is not a claim that the text is *safe*: if the pane
  is a shell and you send Enter, the shell will run it.
- `--key` — repeatable, from a closed allowlist. Anything else is a 400. Max 64
  per call:

  ```
  Enter  Escape  Tab  C-c  C-d  Up  Down  Left  Right  PageUp  PageDown
  ```

  Keys go to the **pane's** input stream — `C-c` reaches the program running in
  the pane, it is not interpreted as a tmux prefix.
- `--enter` — press Enter last. `--enter` together with `--key Enter` sends
  **two** Enters; the CLI warns, because the second one quietly submits a blank
  line at a prompt.
- `--lines` — read-back depth for this one call.

Every accepted call settles ~400 ms, re-captures the pane, and prints the
snapshot. That settle is a heuristic, not a completion signal — a slow command
will not have finished.

### `run NAME "COMMAND" [--timeout S] [--lines N] [--no-bell] [--exit-expr E]`

Wraps the command in a completion sentinel
(`…; rc=$?; …; echo "MUXPLEX_DONE_<token>_EXIT_$rc"`), polls the pane for the
digit-anchored marker, and **exits with the remote command's exit code**.

| Flag | Default | Notes |
|---|---|---|
| `--timeout SECONDS` | `600.0` | Seconds to wait for completion. Subcommand-level — distinct from the global `--timeout`. |
| `--lines N` | `500` | Pane lines polled while waiting. |
| `--no-bell` | off | By default a **non-zero** exit rings the session bell. Leave it on: the bell is the human-facing attention channel, and ringing only on failure is what keeps it meaningful. |
| `--exit-expr E` | `$?` | Shell expression for the exit code, for a non-POSIX shell. |

**`run` assumes the target pane is an idle POSIX shell prompt.** Against vim, a
REPL, `less`, a TUI, an ssh session, or a non-POSIX shell without a matching
`--exit-expr`, it types a garbage line into whatever is actually running and then
hangs until `--timeout`.

Why the marker is digit-anchored: tmux echoes the literal, unexpanded
`…EXIT_$?` into the pane the instant the line is sent, before the shell runs it.
A bare-token substring match would report "done" with a bogus exit code
immediately.

---

## Bells

| Command | Notes |
|---|---|
| `bell ring NAME` | Record a bell fire. |
| `bell clear NAME` | Acknowledge (clear) a session's bell. |

Nothing an agent naturally runs trips a bell — `echo`, `sleep`, and a failing
command all leave `unseen_count` at 0. A bell fires only from an actual BEL byte
(`\a`) reaching the pane, which is what `run` does for you on a non-zero exit.
Ring on failure only; ten successful builds ringing as insistently as one real
failure destroys the channel.

---

## State and settings

| Command | Notes |
|---|---|
| `state` | `active_session`, `active_view`, `settings_updated_at`. |
| `state set [--active-view V] [--active-session S \| --clear-active-session] [--active-remote-id R]` | **Server-global, last-writer-wins — moves the human's view.** At least one flag required. `--active-session` and `--clear-active-session` are mutually exclusive. |
| `settings` | Current settings (`federation_key` and per-remote keys blanked). |
| `settings set KEY VALUE [--allow-destructive]` | One **top-level** key; dotted keys are not supported. `VALUE` is parsed as JSON, falling back to a bare string. Uses a compare-and-swap read-modify-write. |
| `settings sync` | Syncable settings and their timestamps. |
| `settings push FILE\|-` | Push a JSON settings-sync payload from a file or stdin. |

`--allow-destructive` overrides the backstop that refuses a write which would
catastrophically shrink `views` — that backstop exists because a stale client
once PATCHed a whole `views` array back and destroyed 7 of 8 views in one
request. It is a loaded gun; do not reach for it to make an error go away.

**`input_enabled` and `input_allowed_sessions` cannot be set this way.** They are
local-file-only. A write appears to succeed while being silently ignored.

---

## Federation

| Command | Notes |
|---|---|
| `fed ls` | Local and remote sessions merged; per-device failures arrive in-band as a `status` rather than as an error. |
| `fed connect DEVICE SESSION` | Connect to a session on a remote device. |
| `fed new DEVICE NAME` | Create a session on a remote device. |
| `fed rm DEVICE NAME [--yes]` | Delete a remote session. Confirmation gate, same rules as `rm`. |
| `fed bell-clear DEVICE NAME` | Clear a bell on a remote device's session. |
| `fed generate-key [--yes]` | **Rotates this server's federation key, invalidating every existing client** — including whatever configuration the human uses from their phone or Stream Deck. Confirmation gate. |

---

## Maintenance

| Command | Notes |
|---|---|
| `heartbeat [--device-id ID]` | Register or update this device's heartbeat. Defaults to this instance's own `device_id` from `info`. |
| `setup-hooks` | Re-register the tmux hooks. Idempotent and safe to call anytime. Reach for it when `info` reports `bell_hook_armed: False`, or after a tmux server restart — the startup registration is best-effort against a tmux server that may not have been running yet. |

---

## Exit codes

Every client-side failure exits `1` — there is no multi-code convention.

The one deliberate exception is **`run`, which exits with the remote command's
exit code**, so `muxplex-client run build "pytest" && deploy` behaves the way a
shell author expects. A client-side failure *in* `run` itself (unreachable,
forbidden, timeout) still exits `1`.

---

## Error messages worth recognizing

The CLI's stderr is written to be actionable; read it before retrying.

| Situation | What you get, and what to do |
|---|---|
| `403` from `send`/`run` | Prints the literal JSON to add to `~/.config/muxplex/settings.json` and **never suggests a retry**. Relay it to the human and stop — there is no API path around that fence. |
| Session not found | Points at `ls`, and notes that a session created moments ago may not be visible yet (~2s read cache). |
| Auth failure | Names which config tier the federation key came from. |
| TLS verification failure | Includes a remediation hint. The usual cause is trusting the server's leaf certificate instead of the CA — fetch the CA with `ca`. |
| Settings conflict | Someone else wrote concurrently; retry the command. |
| `run` timeout | Prints the last snapshot alongside the token, so you can see what the pane was actually doing. |

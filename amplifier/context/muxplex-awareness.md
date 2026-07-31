# muxplex — driving the user's tmux sessions

This machine may be running **muxplex**, a dashboard over a live tmux server. The
`muxplex-client` CLI drives its whole HTTP API: list sessions, read what a pane
currently shows, type into a live shell, run a command and get its real exit
code, create or kill a session.

## When to use

- The terminal that matters is **not the one you are running in** — a build in
  another window, a deploy sitting on a `[y/N]` prompt, a session someone else
  started.
- The user asks what their tmux sessions are doing, or asks you to answer a
  prompt, interrupt a runaway process, or run something in a named session.
- The user mentions muxplex, `muxplex-client`, session bells, or pane output.

## How to use

**Load the skill first. Do not improvise against `--help`.**

```
load_skill(skill_name="muxplex-control")
```

Everything you type here lands in a real pane on a real machine that a human is
probably also looking at. The skill carries the rules that make that survivable —
the default-closed input fence and what a 403 means, read-before-you-type,
`run` vs `send`, the read cache, the 30-line output cap, and which commands move
the human's own screen. Issuing commands without it is how sessions get mangled.

If `muxplex-client` is not on `PATH`: `uv tool install
"git+https://github.com/bkrabach/muxplex@main#subdirectory=client"`, good once the CLI
lands on `main`. The console script postdates 0.30.1, so plain `uv tool install
muxplex-client` fails today with `No executables are provided by package
muxplex-client`. The skill carries all three forms.

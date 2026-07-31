# muxplex as an Amplifier bundle

An [Amplifier](https://github.com/microsoft/amplifier) bundle that teaches a
session to drive **your** muxplex server. Compose it once and every session can
list your tmux sessions, read what a pane currently shows, answer a prompt that
is waiting, interrupt a runaway process, or run a command in a session and get
its real exit code back.

This is the Amplifier-specific packaging of
[`docs/AGENT_GUIDE.md`](../docs/AGENT_GUIDE.md). That guide is deliberately
vendor-neutral and remains the source of truth for the API contract; this
directory is one harness's wiring around it.

## Install

Add the bundle to your Amplifier configuration — either as an `includes:` entry
in your own bundle, or via `bundle.app` composition in
`~/.amplifier/settings.yaml`:

```yaml
includes:
  - bundle: git+https://github.com/bkrabach/muxplex@main#subdirectory=amplifier
```

Then install the CLI it drives, if you do not have it already. The
`muxplex-client` console script is **new** — it is not in 0.30.1 or any earlier
release, so `uv tool install muxplex-client` against today's PyPI fails with
`No executables are provided by package muxplex-client`. Three forms, each true
in a different state:

| Form | Command | True when |
|---|---|---|
| From a checkout of this repo | `uv run --directory client muxplex-client …` | **Works right now.** No install; run it from the repo root. |
| From the repo on GitHub | `uv tool install "git+https://github.com/bkrabach/muxplex@main#subdirectory=client"` | Once the CLI lands on `main`. Puts the `muxplex-client` executable on `PATH`. |
| From PyPI | `uv tool install muxplex-client` | Once a release newer than 0.30.1 is published. |

That is the whole install. `muxplex-client` reads the same
`~/.config/muxplex/` config the server writes, so on the machine running
muxplex there is nothing further to configure — localhost needs no credential.

Verify from a session:

```
muxplex-client info --verbose
```

## What it wires

| File | Role |
|---|---|
| `bundle.md` | Thin standalone bundle. Frontmatter only — includes the behavior and nothing else. |
| `behaviors/muxplex-control.yaml` | The reusable capability: registers the skill source, injects the awareness pointer. |
| `context/muxplex-awareness.md` | ~200-word always-on pointer: the capability exists, load the skill before using it. |
| [`../skills/muxplex-control/`](../skills/muxplex-control/) | The actual knowledge — command surface, worked examples, and the safety rules. Loaded on demand. |

The skill lives at the **repo root**, not under `amplifier/`, on purpose: it is
a plain [Agent Skills](https://agentskills.io/specification) directory with no
Amplifier-specific frontmatter, so other harnesses can point at it directly.
The bundle registers it by URL:

```yaml
skills:
  - "git+https://github.com/bkrabach/muxplex@main#subdirectory=skills"
```

### Why `bundle.md` has no markdown body

A bundle's markdown body becomes the session's system instruction, and
instructions **replace** on composition (last one wins). A capability bundle
that ships a body would silently overwrite the instruction of whatever it is
composed onto. Context injected via the behavior's `context:` block
*accumulates* instead, which is what an add-on wants. Keeping the body empty is
what makes this bundle safe to compose in any position.

### Why the skill source is a git URL, not a relative path

`tool-skills` resolves local skill sources with
`Path(source).expanduser().resolve()` — relative to the **process working
directory**, not the bundle root. A relative path works from a local checkout
and silently resolves to nothing for everyone who installs from GitHub. The git
URL is the form every other bundle in the ecosystem uses, and the only one that
survives installation.

### Why no tool module

The capability is `muxplex-client`, an ordinary console script the agent invokes
over bash. Wrapping it in an Amplifier tool module would mean:

- a second copy of the command surface, in a different repo's idiom, free to
  drift from the CLI it wraps;
- re-deriving `run`'s exit-code propagation and `--json` output through a tool
  result envelope, losing the shell semantics that make
  `muxplex-client run build "pytest -x" && …` work;
- a Python package plus entry point maintained here, in an ecosystem this repo
  does not own, for no capability the CLI does not already have.

The CLI is the tool; bash is the transport. The bundle's job is discovery and
safety, and it does that with a skill.

## Keeping it in sync

The skill's `#subdirectory=skills` path is a contract with the upstream repo
layout — see the corresponding note in [`../AGENTS.md`](../AGENTS.md).

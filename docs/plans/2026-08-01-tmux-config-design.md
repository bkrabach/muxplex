# Managed tmux Configuration: One Guarded Line, User Always Wins

**Date:** 2026-08-01
**Status:** Design — implemented in this PR
**Author:** bkrabach

## Background

muxplex has always been a *consumer* of whatever tmux config already exists on
the host. It shells out to `tmux` for session lifecycle, reads `TMUX_TMPDIR` to
find the right socket, and registers an `alert-bell` hook — but it has never
shipped or written a line of tmux config.

Two things changed that.

First, `amplifier-cli-tools` is being retired. That package owned the base tmux
config on these machines (`~/.config/amplifier-cli-tools/tmux.conf`, sourced
from a wrapper it wrote into `~/.tmux.conf`) plus the base/local layering
mechanism itself. Its replacement, `microsoft/amplifier-workspace`, deliberately
has no terminal-config surface — it generates ephemeral per-workspace bash
rcfiles and nothing else. When cli-tools goes, that config has no home.

Second, muxplex is the natural home for it. General tmux configuration is not
Amplifier-specific; it belongs with the general tmux tool. muxplex already owns
tmux *behavior* settings (`new_session_template`, `delete_session_template`,
`tmux_socket_dir`, `window_size_largest`) and already has three co-equal
configuration surfaces — the settings modal, `muxplex config`, and the file.
Adding tmux *file* config is a smaller step than it first appears.

## Principles

1. **The user's own config always wins.** muxplex ships opinions, not mandates.
   Anything the user has set, anywhere, overrides anything muxplex sets.
2. **muxplex owns a directory, not the user's file.** Everything generated lives
   under `~/.config/muxplex/tmux.d/`. The user's config file is touched exactly
   once, in one place, reversibly.
3. **Fail loud.** Anything ambiguous raises and stops. There is no degraded path
   and no silent fallback — a config that quietly didn't load is the worst
   outcome, because it looks like muxplex is broken.
4. **Prove it, don't assume it.** Every write is verified by re-reading the file
   *and* by starting a real tmux server. "The code says it worked" is not
   evidence.

## Measured facts this design rests on

Each of these was measured on tmux 3.4, not assumed. The first one inverted an
earlier draft of this design.

**tmux loads EVERY user config in its search path, not just the first found.**
Order is `/etc/tmux.conf`, then `~/.tmux.conf`, then
`$XDG_CONFIG_HOME/tmux/tmux.conf`. When two files set the same option, the
later one wins. This is easy to get backwards: setting the *same* option in both
files and observing which value survives cannot distinguish "only the XDG file
loaded" from "both loaded and XDG won." Distinct sentinel options in each file
settle it — both load.

**Therefore: install into `~/.tmux.conf` (earliest), at the TOP of the file.**
That puts muxplex first in the whole chain, so everything the user has — later
in that file, or anywhere in their XDG config — overrides it. Installing into
the XDG file, or at the bottom, would silently make muxplex outrank the user.
This is deliberately the opposite of the conda/rustup/nvm convention: they
install last because they want to win; we install first because we want to lose.

**`source-file -q <glob>` is a true no-op when nothing matches.** A missing
directory or empty glob does not emit an error and does not abort the rest of
the config. Glob support in `source-file` needs tmux >= 3.0, which is gated.

**`os.replace()` onto a symlink path replaces the symlink with a regular file.**
A tmux config is very often a symlink into a tracked dotfiles repo. The naive
"atomic write" would silently detach that repo from the user's home. Resolving
first, and writing the real file, preserves the link.

## Design

### The managed block

Exactly one marker-delimited block, at the top of `~/.tmux.conf`:

```
# >>> muxplex managed block >>>
# Managed by muxplex. Do not edit between these markers -- your changes will be
# overwritten. Put your own tmux settings anywhere BELOW this block (they win),
# or in ~/.config/muxplex/tmux.d/90-local.conf.
# Remove with: muxplex tmux uninstall
source-file -q ~/.config/muxplex/tmux.d/*.conf
# <<< muxplex managed block <<<
```

Markers make the operation idempotent (a stale block is replaced in place, never
duplicated) and make uninstall exact (remove the block, touch nothing else).

### The fragment directory

```
~/.config/muxplex/tmux.d/
  10-muxplex-base.conf   rendered by muxplex; overwritten on every install
  20-theme.conf          rendered from the tmux_theme setting; overwritten
  90-local.conf          created ONCE; muxplex never writes it again
```

Numeric prefixes give deterministic order. `90-local.conf` is the user's escape
hatch inside muxplex's own namespace — it loads last, so it beats both muxplex
fragments, and a theme switch or version upgrade can never clobber it.

### Settings

One new key, `tmux_theme` (default `"brand"`), selecting a file from
`muxplex/tmux_templates/themes/`. Shipped themes:

| Theme | Palette |
|---|---|
| `brand` | muxplex's own UI tokens — `#0D1117` base, `#00D9F5` cyan accent, `#F1A640` bell amber |
| `steel` | the retired amplifier-cli-tools palette |
| `catppuccin-mocha` | Catppuccin Mocha |

`brand` is the default because it makes the terminal and the dashboard read as
one product — most concretely, `window-status-bell-style` uses the *same* amber
the dashboard already uses for bells, so a window that rings turns amber in both
places at once. One signal, one colour, two surfaces.

**`tmux_theme` is deliberately NOT in `SYNCABLE_KEYS.`** It renders to a file on
*this* host, making it exactly as machine-scoped as `tmux_socket_dir`. Syncing
it would also make every theme tweak bump the shared `settings_updated_at` that
arbitrates `views` LWW races — the precise coupling `views_updated_at` was
introduced to break.

It is also **not** in `LOCAL_ONLY_KEYS`. That fence exists because a Bearer-key
holder must not be able to widen a security boundary; picking between three
shipped, in-repo colour files is not a boundary. Note the fence question *would*
apply to a future free-text directive editor — tmux config can carry `run-shell`
and `default-command` — so that feature must revisit this decision rather than
inherit it.

### Safety properties

| Property | Mechanism |
|---|---|
| Backup before touch | timestamped copy beside the file, never clobbering an existing backup |
| Atomic write | tmp + `os.replace`, the `state.py`/`manifest.py` pattern (not the non-atomic `settings.py` one) |
| Symlink safety | refuse by default with the real target named; `--allow-symlink` resolves first and preserves the link |
| Content preservation | after writing, the file minus the block is compared to the original and mismatch raises |
| Load verification | a throwaway tmux server on a **private socket** reads back an `@muxplex_loaded` sentinel |
| Immediate effect | `source-file` into the running server, so the user sees it work without restarting |
| Reversible | `uninstall` removes exactly the block and restores the file byte-for-byte |

The verification step is the reason this is safe to run at all: muxplex writes,
then *proves* tmux accepts the result, rather than reporting success because the
write returned no error.

### Why not print instructions instead of writing?

An earlier draft did exactly that — print the line and let the user paste it —
on the grounds that muxplex has no precedent for editing a file it did not
create (`service.py` writes systemd/launchd files, but it creates and owns them
whole), and that `muxplex env` establishes a "print, don't write" posture.

That was rejected for one reason: **people don't read install instructions.** A
step that most users skip produces a silent, self-inflicted "muxplex is broken"
— exactly the failure mode principle 3 exists to prevent. The precedent is worth
departing from, but only with the full safety apparatus above, which is why that
apparatus is not optional.

## UI changes

None in this pass. The CLI and the settings key are the surface; the settings
modal tab is deliberately deferred until the mechanism has real use. Per
`AGENTS.md`, capability lands in the API/CLI first, never as frontend-only
state.

## Test plan

### New tests (`muxplex/tests/test_tmux_config.py`)

Every test redirects the module-level path constants at `tmp_path`, so the real
home is never touched, and every tmux invocation uses a private `-L` socket, so
live sessions are never at risk. Tests needing the tmux binary skip without it.

- Ground truth: both user configs load; the later one wins conflicts
- Install target is always the earliest-loaded file
- Creates a config when none exists; tmux loads it
- Preserves an existing file byte-for-byte, block at top, backup verified
- Never edits the user's XDG file, and the XDG prefix still wins
- Tolerates a file with no trailing newline
- Idempotent across three runs; no backup churn on a no-op
- Replaces a stale block in place rather than duplicating it
- Dry run writes nothing but produces a real diff
- Unknown theme raises
- Refuses a symlinked config by default, naming the real target
- `--allow-symlink` writes through and **preserves the symlink**
- Uninstall restores byte-for-byte; second uninstall is a clean no-op
- Status flags a block sitting in a later-loaded file
- `90-local.conf` is created once and never rewritten
- Every shipped theme renders **and actually loads in tmux**
- `verify()` reads back the sentinel; `apply_live()` no-ops without a server

### Updated tests

`test_readme.py` already enforces that every non-underscore `DEFAULT_SETTINGS`
key appears in the README; the `tmux_theme` row satisfies it.

## Out of scope

- A settings-modal tab for tmux config (deferred until the mechanism is used)
- Exposing individual tmux options as settings — the theme selector proves the
  settings → fragment → live pipeline; more knobs follow the same path
- Free-text directive editing (revisit the `LOCAL_ONLY_KEYS` question first)
- wezterm, yazi, and lazygit config, which the cli-tools retirement also orphans

## Open questions

- Should `muxplex tmux install` be offered during first-run/`doctor` rather than
  waiting to be discovered? It is the same "people don't read directions"
  problem one level up.
- `~/.config/muxplex/` is hardcoded via `Path.home()`, consistent with the rest
  of muxplex. `AGENTS.md` lists honouring XDG as a candidate future fix; this
  feature adds one more caller to migrate when that happens.

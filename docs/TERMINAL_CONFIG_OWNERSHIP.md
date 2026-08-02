# Terminal Config Ownership — Decisions, Outcomes, and What Is Still Open

**Status:** Record. Not a proposal, not a draft.
**Date:** 2026-08-02

This began as a proposal written for review while `amplifier-cli-tools` was being
retired, at a point when nothing had been built. Most of it has since shipped —
v0.31.0 through v0.33.0 — and the questions it raised have mostly been answered,
several of them by building the thing rather than by arguing about it.

It is preserved here because the analysis outlived the proposal. The verified
findings in §2 are the evidence base for why muxplex writes tmux config at all,
and they are still true; the reasoning behind the closed API vocabulary in §5.4
is why `PATCH /api/tmux-config` looks the way it does. The original lived only in
a scratch workspace that no longer exists.

Sections read as: the decision, then its outcome. Nothing has been deleted for
being done.

---

## 1. The situation that produced this

Four projects produced the terminal environment on this machine. One was being
retired.

| Project | Role then | Fate |
|---|---|---|
| `bkrabach/amplifier-cli-tools` | **Actual owner** of base tmux config, wezterm config, yazi config, and the wrapper/layering system | **Retired and archived** |
| `microsoft/amplifier-workspace` | Workspace scaffolding + its own from-scratch tmux session generator | Replacement for amplifier-cli-tools |
| `bkrabach/muxplex` | Web dashboard for tmux sessions. Owned tmux *behavior* config, owned zero tmux *file* config | Became the new home for general tmux |
| `bkrabach/dotfiles` | Personal layer: shell, git, themes, secrets, scripts, systemd | Stays personal |

When amplifier-cli-tools retired, these became **homeless**:
- The entire base tmux config (~30 directives then live on this machine)
- The 333-line wezterm config (tab colors, `name:color` tab convention, bell highlight system, rename picker)
- The yazi behavior config
- The base/local **layering mechanism itself** (the `source-file` wrapper + `dofile` merge)

`amplifier-workspace` has **no terminal config surface at all** and does not want
one — it generates ephemeral bash rcfiles per workspace and nothing else.

Of that list, tmux found a home. The rest did not; see §5.2 and §7.

---

## 2. Verified findings that constrained the design

All verified against live machine state and source, not inferred. Retained
unedited except for inline `→` notes recording where a finding has since been
acted on.

### 2.1 The layering already exists and works — for two tiers only

Live `~/.tmux.conf` is a 9-line wrapper built by **two different tools**:

```
source-file ~/.config/amplifier-cli-tools/tmux.conf              # base, UNGUARDED
if-shell "[ -f .../tmux.conf.local ]" "source-file .../tmux.conf.local"   # local, GUARDED
source-file ~/dotfiles/amplifier-cli-tools/tmux.conf.local       # personal, UNGUARDED
```

Lines 1–6 written by `amplifier-dev setup`. Lines 7–9 appended by
`dotfiles/bin/setup_common.sh:333`.
There is **no tier for "someone else's preferences."** That is the gap.

→ Acted on. `~/.config/muxplex/tmux.d/` is that tier (§3.2), and the single
guarded line replaces the unguarded ones.

### 2.2 Three pairs of duplicated implementations

1. **amplifier-cli-tools `tmux.py` vs amplifier-workspace `tmux.py`** — near-complete reimplementation. Divergences that produce different behavior for the same directory: session naming (raw basename vs sanitized+32-cap), shell window (2 panes vs 1), missing-tool handling (graceful degrade vs hard fail), pre-attach escape flush (present vs dropped), resurrect cleanup (present vs dropped), venv activation (both dropped it from the bash originals).
2. **dotfiles `bin/tmux-amplifier-dev.sh` vs `bin/amplifier-claude-dev.sh` + `lib/tmux-dev-lib.sh`** — the former is a pre-refactor copy of the latter two. Dead weight.
3. **dotfiles systemd snapshot/restore vs muxplex `manifest.py`/`restore.py`** — both implement tmux session persistence and recreation. muxplex already has a "lost epoch" concept for rebuilding sessions after a server death.

→ Partially reconciled. Pair 3 is not: both systems are still armed, deliberately
(§7).

### 2.3 The wezterm stack is broken in four places

- **The 333-line amplifier wezterm template is dead on every Windows client.** `setup_windows.ps1:145` (`Copy-Item -Force`) and `setup_common.sh:306` (WSL→Windows mklink) both plant the *dotfiles* 119-line config at the Windows-side `~/.wezterm.lua`, overwriting the wrapper. The amplifier template is faithfully rewritten to `~/.config/amplifier-cli-tools/wezterm.lua` — an orphaned file nothing loads.
- **Injection ordering hazard.** `setup.py:348-367` injects `local config = amplifier_config` before `return config`, which *shadows* the dotfiles file's own `config` — silently discarding ~100 lines of settings.
- **WSL split-brain.** amplifier-cli-tools deploys to the Windows home; dotfiles looks for the file in the WSL home. Dotfiles customizations never reach the file wezterm actually reads.
- **Idempotence guard never matches** (`setup_common.sh:357`) — the block is re-injected on every run.

Also: dotfiles vs amplifier wezterm keybindings are **inverted**
(split-horizontal/vertical swapped; close targets pane vs tab).

→ Not acted on. wezterm has no owner (§5.2, §7).

### 2.4 muxplex already owns tmux *behavior*, has three config surfaces, and has scar tissue

- Config: `~/.config/muxplex/settings.json`, 35-key flat schema, single write choke point (`save_settings()`).
- Surfaces: a 5-tab web modal (`<dialog>` + left rail), a full `muxplex config get/set/list/reset` CLI, and direct file editing. All three are co-equal.
- **Already owns:** `new_session_template`, `delete_session_template` (arbitrary shell, `{name}` substituted), `tmux_socket_dir` (TMUX_TMPDIR override), `window_size_largest`, and a globally-registered tmux `alert-bell` hook.
- **Never writes any tmux config file** — confirmed by grep.
- **Scar tissue that must be respected:** a stale browser tab once PATCHed a whole `views` array and destroyed 7 of 8 views → hence the destructive-write backstop and 20-deep settings-history rotation. A `systemctl restart` once destroyed 44 live sessions → hence `KillMode=process` and `cgroup_escape.py`. AGENTS.md: *"The user's tmux sessions are the product... not recoverable."*
- **Binding rules:** API-first, frontend second, never frontend-only. `LOCAL_ONLY_KEYS` exists because *API auth ≠ operator authority*. No server-side type validation exists. `save_settings()` is non-atomic. `test_frontend_js.py` has 229 source-text assertions that trip on frontend refactors. Never run tests on a live host.
- **Adding a settings tab is ~15 lines**: one `<button data-tab="x">` + one `<div class="settings-panel" data-tab="x">`. The tab switcher needs no change.
- **Closest existing analog** to a config editor: the deck PWA's Export/Import JSON + Reset triad (`deck/index.html:143-155`) — deliberately kept local-only to avoid federation sync complexity.

→ Two of these have changed. "Never writes any tmux config file" stopped being
true at v0.31.0. "No server-side type validation exists" was the reason
`tmux_theme` and `tmux_copy_mode` are each validated against a closed set rather
than trusted as flat-blob values (§5.3, §5.4). The 15-line tab estimate held: the
Terminal tab is the sixth.

### 2.5 Proven mechanism

`source-file -q <glob>` works on tmux 3.4 (tested live): glob expands, files load
in lexical order, **a missing directory or zero matches is a silent no-op that
does not abort the rest of the config.** That is the guard.

WezTerm's equivalent, `wezterm.glob()`, is documented and **used nowhere** in any
of these repos. All three wezterm layering implementations hand-roll a fixed
two-file `dofile` chain instead.

→ This is the mechanism that shipped, unchanged.

### 2.6 Security — prerequisite, not a step

Committed to git and pushed to GitHub:
- `~/dotfiles/env.sh` — ~20 live API keys/tokens (OpenAI, Anthropic, Google, xAI, Brave, Bing, RunPod, Slack, M365 client secret, PyPI publish token, a Postgres DSN)
- `~/dotfiles/.azlin/home/.ssh/github-vm` — an SSH **private key**, mode `-rw-rw-r--`
- `~/dotfiles/.azlin/home/.bashrc.local` — a live GitHub OAuth token

The `.azlin` README notes this placement was chosen because it *"passes through"*
azlin's secret scanner. Rotate and purge before any sharing work ships.

→ Explicitly accepted as a risk by the owner, on the grounds that the repository
is private. Unchanged otherwise. It remains a prerequisite for any *public*
sharing work.

---

## 3. The ownership model, and what shipped of it

### 3.1 The split

| Domain | Owner | Rationale | Outcome |
|---|---|---|---|
| **tmux config file** (terminal/color, behavior, clipboard, indexing, copy-mode, keybindings, status bar, titles) | **muxplex** | It is general-purpose tmux, not amplifier-specific. muxplex already owns tmux behavior and has three mature config surfaces. | **Shipped** v0.31.0 |
| **tmux socket / session templates** | **muxplex** (already did) | No change. | No change |
| **tmux session persistence** (snapshot/restore) | **muxplex** | It already has `manifest.py`/`restore.py`/lost-epoch. Retires the broken dotfiles systemd path. | **Not migrated** — §7 |
| **Workspace scaffolding** (submodules, AGENTS.md, `.amplifier/settings.yaml`) | **amplifier-workspace** | Already does. No change. | No change |
| **Amplifier dev session layout** (`amplifier`/`shell`/`git`/`files` windows, resume detection, rcfile generation) | **amplifier-workspace** | Workflow-specific. But should *consume* muxplex's tmux config rather than assume its own. | No change |
| **Optional tool install** (lazygit, yazi, mosh) | **amplifier-workspace** | Already does. | No change |
| **wezterm config** | **UNRESOLVED** | No natural owner exists after retirement. | **Still unresolved** — §5.2 |
| **yazi / lazygit themes** | **UNRESOLVED** | Same problem, smaller stakes. | Still unresolved |
| **Shell (prompt, PATH, completions, env), git identity/aliases, personal paths, secrets** | **dotfiles** | Stays personal. Secrets become `env.sh.example`. | No change |

### 3.2 The mechanism: managed fragment, never the user's file — **shipped v0.31.0**

muxplex must **never own `~/.tmux.conf`**. It owns a directory inside its own
namespace, consistent with everything else it does:

```
~/.config/muxplex/tmux.d/
  10-muxplex-generated.conf     <- written from settings.json; regenerated on save
  20-<preset-name>.conf          <- opt-in shared presets; plain files
  90-local.conf                  <- user's own; muxplex never writes this
```

The user's `~/.tmux.conf` receives exactly **one** line, inside a marker block,
added only by an explicit opt-in command:

```
source-file -q ~/.config/muxplex/tmux.d/*.conf
```

Properties:
- **Additive** — empty directory changes nothing.
- **Guarded** — `-q` makes missing files a silent no-op (proven, §2.5).
- **Ordered** — numeric prefixes give deterministic precedence; user's `90-` always wins.
- **Toggleable** — disable a preset by `rm` or rename to `.conf.off`.
- **Auditable** — plain text, readable before enabling.
- **Reversible** — `muxplex tmux uninstall` removes the line and restores the backup.
- **Distribution-agnostic** — git clone, curl, manual copy. Mechanism doesn't care.

Install flow (`muxplex tmux install`):
1. Detect existing `~/.tmux.conf`. If present, **timestamped backup first.**
2. Show the exact diff. Require confirmation (or `-y`).
3. Append the single guarded line.
4. Never touch anything else in the user's file.

The `20-<preset-name>.conf` slot exists in the ordering and is unused; see §3.4.

### 3.3 The surfaces

Per muxplex's AGENTS.md rule — **API first, frontend second, never
frontend-only**:

1. **API** — **shipped v0.33.0.** `GET`/`PATCH /api/tmux-config`, returning
   install status, current theme and copy mode, available themes, and a preview
   of the rendered config, so unseen consumers (deck sidecar, federation peers,
   agents) get it too.
2. **CLI** — **shipped v0.31.0**, partially. `muxplex tmux status|install|uninstall`.
   The proposed `preview` and `preset` subcommands were **not built**: `preview`
   because the web tab's disclosure covers it for the actual audience, `preset`
   because there are no presets (§3.4). Config values themselves flow through the
   existing `muxplex config set` path.
3. **Web UI** — **shipped v0.33.0.** A sixth settings tab, "Terminal": theme
   picker, copy-mode choice, install status, and the generated config behind a
   disclosure. The proposed Export/Import/Reset triad was not built; with a
   two-key closed vocabulary there is nothing to export that `muxplex config`
   does not already show.

The storage-model question flagged here for review is answered in §5.3.

### 3.4 Presets, and what "sharing Brian's setup" means — **parked**

The plan was a first preset, `preset:bkrabach`, extracted from the
dotfiles/amplifier-cli-tools union: the OPINIONATED-tagged rows (Catppuccin
theme, Alt+hjkl vim pane nav, 1-based indexing, vi copy-mode, Alt/Shift nav) as a
preset, the UNIVERSAL rows as the muxplex default, the PERSONAL rows staying in
dotfiles.

Never built, and the reason is the more useful part of the record.

The audience was clarified: **non-technical desktop users, not tmux purists.** A
preset picker exists to let a purist opt *out* of one person's opinions. If
nobody in the audience is a purist, that is a decision nobody wants to be asked
to make — it is a choice presented to someone with no basis for choosing. And the
rare power user is already served: `90-local.conf` is loaded last and is never
written by muxplex, so four lines there beat anything the preset system could
have offered them.

That clarification did not just cancel the preset tier; it reshaped the default
itself. If there is no opt-out layer, the default has to be right for the actual
audience on its own — which is what v0.32.0 did (§6).

The `20-` slot remains in the load order. A preset is still just a plain `.conf`
file dropped there. Nothing needs to be built to enable that if it is ever
wanted.

---

## 4. What this fixes, concretely

| Broken then | Now |
|---|---|
| Unguarded `source-file` → tmux hard-fails if `~/dotfiles` is absent | Single `-q` guarded line — **shipped** |
| No tier for others' preferences | `tmux.d/` tier exists — **shipped**; no presets occupy it (§3.4) |
| Two duplicate tmux session generators | Partially reconciled (§2.2) |
| Two duplicate session-persistence systems, one of them broken (`AWS_SRC` points at a nonexistent path) | **Not yet** — both still armed, deliberately (§7) |
| Config vanishes when amplifier-cli-tools retires | Config has a maintained home — **shipped** |
| Vim pane nav (`M-hjkl`) silently missing from the live config | Resolved by being dropped on purpose, with a documented restore recipe (§6) |
| No backup when a tool touches your `~/.tmux.conf` | Timestamped backup + diff + confirm — **shipped** |

---

## 5. The open questions, and their answers

### 5.1 Is muxplex the right home for tmux *file* config at all?

The worry was scope creep: muxplex is branded and documented as a *session
dashboard*, and this expands it from "views and controls tmux sessions" to
"configures tmux." AGENTS.md contained no scope statement forbidding it — and
none anticipating it either.

**Answered by building it.** Shipped in v0.31.0 and in use. General tmux
configuration belongs with the general tmux tool, and the same audience that
drives sessions from a web dashboard is the audience least able to hand-edit
`~/.tmux.conf`.

### 5.2 Where does wezterm go?

**Still open.** Deferred by the owner, unresolved. It has no owner after
retirement. muxplex is a web dashboard and has nothing to do with a native
terminal emulator; amplifier-workspace does not touch terminals. The options as
stated then: (a) muxplex expands to "terminal environment" — contradicts its
identity; (b) a new small tool; (c) stays in dotfiles, published as a shareable
preset repo; (d) let it die and accept per-user wezterm config. Note the amplifier
wezterm template was **already dead on Windows** (§2.3) — so the question of how
much is actually being lost remains a live one.

### 5.3 Storage model — nest in the flat blob, or separate?

The hazard: the flat 35-key `settings.json` has whole-value PATCH semantics and
no type validation — the exact shape that destroyed 7 of 8 views.

**Answered: neither nested nor a separate store.** Two narrow keys,
`tmux_theme` and `tmux_copy_mode`, each **separately validated against a closed
set**. They live alongside the other settings but do not inherit the untyped
blob's semantics, because nothing about them is free text. Both are
machine-scoped and do not sync between devices.

### 5.4 Blast radius — should the write path sit behind `LOCAL_ONLY_KEYS`?

**Answered structurally instead, which is stronger.**

tmux config can carry `run-shell` and `default-command` — arbitrary code
execution — and the API bearer key is the same credential handed to remote
agents. So rather than fencing the write path by *caller*, the **writable
vocabulary is closed**: `theme` is validated against the shipped theme list,
`copy_mode` against exactly two values, and anything else is rejected with 400
before it reaches disk. There is no free-text directive field and there should
not be one.

Verified against a live server with four injection attempts — a `run-shell`
payload, a newline-smuggled `default-command`, a path-traversal theme, and a
plausible-but-invalid enum value. All four rejected; nothing written.

### 5.5 Sequencing

**Resolved.** amplifier-cli-tools is retired and archived. The secrets question
(§2.6) was explicitly accepted as a risk by the owner on the grounds that the
repository is private. The duplicate implementations are partially reconciled.
The preset tier was parked (§3.4) and the tmux config work went first.

### 5.6 Is the whole thing worth it?

**Answered by shipping.** The framing at the time — "others can adopt my terminal
setup safely" — turned out to be the wrong measure, and it is worth recording
why. The audience clarification in §3.4 moved the value from *sharing one
person's config* to *shipping a sane default to people who never asked to learn
tmux*. That is a larger group and a clearer win, and it is what v0.32.0 acted on.

---

## 6. What happened that this document did not anticipate

**v0.32.0 retargeted the default at desktop users.** Once the preset tier was
parked, the default had to stand on its own for people who have never heard of
tmux. Copy mode became `emacs` rather than `vi` — arrows, PageUp/PageDown, and
Home/End do the obvious thing, and there is no modal `v`/`y` selection with
nothing on screen indicating a mode exists. `Ctrl+C` copies the selection and
`Esc` leaves copy mode, because scrolling up silently enters copy mode and
without an obvious way out the common report is "my typing stopped working."
`word-separators` was widened so a double-click grabs a whole path, filename, or
package name instead of a fragment. Vim-style `Alt+hjkl` pane navigation was
dropped from the default as muscle memory this audience does not have;
`Alt+arrows` stays, being discoverable by trying it. Kept deliberately: mouse on,
50k scrollback, 1-based indexing, and `renumber-windows`.

**A real user-facing bug surfaced along the way and was fixed.** X.509 caps a
CommonName at 64 characters, and muxplex passed the hostname in unchecked — so on
any host with a longer name, `setup-tls` could not generate a certificate at all.
Truncating the CN is safe because clients validate subjectAltName, which still
carries the full hostname.

**muxplex's macOS CI job had been failing on every commit since it was
introduced.** It is now green, and real `launchctl` test coverage was added where
every launchd test had previously been mocked.

---

## 7. Still open

**wezterm ownership.** §5.2. Deferred, unresolved, no owner.

**Session persistence migration to muxplex.** The precondition is now met; the
migration is not done.

The detail worth recording is why it was not simply switched on. muxplex's
restore is **cold-start-only** and keys off a per-session `created_with` field
recording which command pair recreates that session. That field was **empty**. A
restore would therefore have rebuilt all 51 sessions from the default template
instead of their real workspace layouts — precisely the "looks restored and
isn't" failure that muxplex's own `restore.py` warns about.

It has since been backfilled: 50 sessions to an `amplifier-workspace` command
pair driving `~/dev/{name}`; 1 to a dedicated pair for a nested path whose
directory basename differs from its session name; 1 correctly left alone because
it is not a workspace session. A real restore was then proven end-to-end against
a throwaway session — it came back with windows `amplifier shell git files` and
the correct cwd.

The dotfiles systemd snapshot/restore units remain armed alongside muxplex
**deliberately**. Redundant but safe, pending one real reboot proving muxplex's
path before either is disabled.

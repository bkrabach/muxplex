# Managing session command pairs from the muxplex UI — design

Status: **SHIPPED to `main` on 2026-08-02 (`1f714a3`, then `b82e4bf`) — NOT YET
IN A RELEASE.** The newest tag, `v0.33.0`, predates both commits, so an
installed muxplex does not yet have any of it. Retained as an architectural
decision record.

**The conclusion was: build the authoring aid, never a write path** — and it
holds permanently, not until someone finds a cleverer API shape. `session_commands`
holds arbitrary shell that the server itself executes via
`create_subprocess_shell` (`sessions.py:511`), and `AuthMiddleware`'s loopback
source-IP bypass precedes every credential check (`auth.py:285`) with no CSRF
defense — so any endpoint meaning *define a pair* is remote code execution
reachable from a page in the operator's own browser. The design's job was to
make that limit feel like a doorway rather than a wall, which it can, because
the safe path has no expressiveness ceiling: the authoring aid composes
arbitrary shell, it just doesn't submit it.

That is also why §4.2's finding matters more than its size suggests: **a
working-directory parameter is not a value, it is a capability.** Control of a
process's cwd is control over what that process reads — `.envrc`, `Makefile`,
`.git/config` (`core.pager` / `core.fsmonitor` / `core.sshCommand` are
documented execution vectors). An API verb that sets cwd for a root-executed
command is a define verb wearing a hat.

**What shipped:**

- `1f714a3` — the CLI half. `muxplex config set|reset` on a `LOCAL_ONLY_KEYS`
  key had been printing success while `patch_settings()` silently wrote nothing;
  it now exits non-zero naming the real escape hatch. This document found that
  bug (§Verdict) and refused to point users at a lie. The same commit added
  `muxplex commands list|add|remove`, which is what makes the design's central
  move possible — **the thing you copy is a command, not JSON.**
- `b82e4bf` — the frontend half: all six §6 changes. Always render the list
  including `default` (item 1), re-fetch on `openSettings()` (item 2),
  per-pair `Duplicate…` / `Copy command` with a live `muxplex commands add`
  line and no Save button anywhere (item 3), the absence-of-Save copy reworded
  as capability rather than apology (item 4), an error badge outside the dialog
  (item 5), and a non-selectable warning row in the New Session picker
  (item 6).

**What it deliberately declined to build** — §9 records these so they are not
re-proposed as oversights: no API verb that defines a pair; no
`POST /api/session-commands/validate` ("a write-shaped endpoint that doesn't
write is an invitation"); no client-side reimplementation of V1–V7 as
authority; no automated typing into the terminal; no federation of
`session_commands`; no `settings.json` locking; no new `DEFAULT_SETTINGS` key.
§6 separately declined a `+ New pair` blank-form button as surface without
demand. **API contract impact: none** — no endpoint added, removed, or
reshaped, which is the strongest available evidence the design respected the
constraint rather than routing around it.

This is the UI follow-on to `2026-08-02-named-session-command-pairs-plan.md`,
which established the fence it works within. See also AGENTS.md → "Terminal
input" and `docs/API_SEMANTICS.md` → "Named session command pairs".

The source tree cites this document as **`COMMAND_PAIRS_UI_DESIGN.md`** — its
name in the workspace where it was written — from `frontend/app.js` and
`frontend/style.css`, by section and item number. Those citations refer to
this file.

---

## Verdict, up front

**Build the authoring aid. Do not build a write path. And fix the CLI, because the escape hatch
you are about to point users at is currently a lie.**

The recommendation is closest to your floor option ("better docs plus a copy-to-clipboard
snippet") but with one change that makes it a real workflow instead of a consolation prize:

> **The thing you copy should be a command, not JSON.**

That requires `muxplex commands add` to exist, and it requires fixing a live bug where
`muxplex config set` on a fenced key **prints success and writes nothing**
(`cli.py:1591` calls `patch_settings()`, which skips `LOCAL_ONLY_KEYS` at `settings.py:676-687`;
`cli.py:1592` then prints the value unconditionally). Today, a user told "manage pairs at the
config layer" will reasonably try `muxplex config set session_commands '[...]'` first, be told it
worked, and find it didn't. That is a fail-silent path in a repo whose stated principle is failing
loud, and it sits directly in the path of this feature.

Three findings drove this, in descending order of importance:

1. **The tmux precedent is already fully spent on this feature.** Its safety comes from the caller
   contributing *a selector into a set the caller cannot extend*. muxplex already applied exactly
   that shape to command pairs — `command_id` selects, the file defines. There is no further
   portion of the precedent left to port, because the part that cannot port is the part that gives
   this feature its value. (§2)

2. **The "different paths" need is not a parameterization problem. It is an authoring-tedium
   problem.** The user has five project directories and does not want to hand-write five
   near-identical JSON blobs over SSH. Tedium is fixable entirely on the read side of the security
   boundary, at zero security cost. (§3)

3. **A working-directory parameter is not a value; it is a capability.** Control over a process's
   cwd is control over what that process reads — `.envrc`, `Makefile`, `.git/config`
   (`core.pager` / `core.fsmonitor` / `core.sshCommand` are documented execution vectors),
   `.tool-versions`, `docker-compose.yml`. muxplex runs as root on `tower`. Under the settled
   threat model, an API verb that sets cwd for a root-executed command is a define verb wearing a
   hat. (§4.2)

**What this does not do:** it does not let the user click Save. Nothing in this design writes
settings from the browser. That limit is permanent under the constraint, and the design's job is
to make it feel like a doorway rather than a wall — which it can, because the safe path has **no
expressiveness ceiling**. The authoring aid composes arbitrary shell. It just doesn't submit it.

---

## 1. What is actually true today (verified, not assumed)

| Fact | Where |
|---|---|
| `session_commands` is in `LOCAL_ONLY_KEYS` | `settings.py:207-218` |
| `patch_settings()` skips fenced keys, applies the rest of the patch, logs a warning | `settings.py:676-687` |
| `save_settings()` applies **no** fence — it writes whatever it's given | `settings.py:566-590` |
| `load_settings()` applies no fence — a file edit takes effect immediately | `settings.py:271-288` |
| Templates run through `create_subprocess_shell` | `sessions.py:511` |
| Validation is id charset, label length, `{name}` presence, duplicate ids (V1–V7) | `settings.py:329-440` |
| Loopback source-IP bypass precedes every credential check | `auth.py:285` |
| `GET /api/session-commands` returns `{commands, default_id, errors}` | `main.py:1071-1094` |
| A pair that no longer resolves makes its sessions **undeletable** (409) until fixed | `main.py:~1685`, `API_SEMANTICS.md` |
| Settings > Commands is the `data-tab="new-session"` panel | `index.html:275-296` |
| The extras list **hides itself entirely** when no extras are configured | `app.js:4399` |
| The list **filters out** the `default` pair | `app.js:4381` |
| `loadSessionCommands()` runs **once**, at page init — never re-fetched | `app.js:5549` |
| `loadTmuxConfigSettings()` **is** re-fetched on every `openSettings()` | `app.js:4361` |
| `muxplex config set` on a fenced key prints success and writes nothing | `cli.py:1591-1592` |
| There is no lock on `settings.json`; concurrent writers race, last one wins | `settings.py` (no locking) |
| Every write is snapshotted to `settings-history/`, 20 kept | `settings.py:508-563` |

Two of these are load-bearing and easy to miss:

- **A file edit is live on the next request.** `resolve_session_commands()` calls `load_settings()`
  every time, and `GET /api/session-commands` resolves fresh. No restart, no reload of the server.
  The only stale link in the chain is the **browser**, which fetched once at page load. That means
  the file→UI feedback loop is already 95% built; it's missing one re-fetch.
- **The extras panel hides when empty.** A user who has never configured a pair sees *nothing* in
  Settings > Commands about pairs existing. The feature is invisible to exactly the person who
  needs to discover it. This is the single biggest discoverability defect and it costs one line to
  fix.

---

## 2. The tmux precedent: what carries, and what cannot

`PATCH /api/tmux-config` (`main.py:1978+`) is safe because of a property worth naming precisely:

> **The API caller contributes a selector into a set the caller cannot extend.**

- `theme` must be in `tmux_config.available_themes()` — the stems of `.conf` files shipped
  *inside the installed package* (`tmux_config.py:185-187`). A caller cannot add one without write
  access to the package on disk.
- `copy_mode` must be in `COPY_MODES` — a literal tuple in source (`tmux_config.py:195`).
- The rendered bytes come from developer-authored files. The user contributes **zero bytes** of
  directive text. Hence the docstring: *"There is no free-text field here and there must never be
  one."*

Note that `tmux_theme` and `tmux_copy_mode` are deliberately **not** in `LOCAL_ONLY_KEYS`
(`settings.py:207-218`). That is the proof: a settings key *may* be API-writable when its value
space is closed and developer-authored.

**What carries over to command pairs:** the shape "API selects, file defines."

**muxplex already did this.** `POST /api/sessions {"command_id": ...}` is exactly that selector.
`GET /api/session-commands` is exactly the enumeration of the closed set. The precedent has already
been applied to this feature, in full, and it shipped.

**What cannot carry over:** the closed set itself. For themes, the developer can enumerate the
useful values in advance. For command pairs, **the feature's entire value is the arbitrariness the
precedent eliminates.** "Someone else wants to use a different command all together" is a
requirement for unbounded text. There is no finite developer-authored set of "commands the user
might want to run," and the moment you claim there is, you have either broken the user's use case
or reinvented free text.

So: there is no remaining lesson to port. The correct conclusion from studying `68e3103` is not
"do the same thing here" — it's "the same thing was already done here, and the residue is
irreducible."

---

## 3. Reframing the requirement

The two stated needs:

- **N1** — *"I want to use w/ diff paths"*: same command shape, different working directory.
- **N2** — *"someone else wants to use a diff command all together"*: genuinely arbitrary shell.

N2 is unambiguously file-only. Nothing can change that under the constraint.

N1 looks like parameterization, and §4.2 shows why routing it through the API is not safe. But the
more useful observation is that **N1 as experienced by the user is not a runtime problem at all**.
The user is not asking to choose a directory at create time. They are asking not to hand-write
this five times over SSH:

```json
{"id": "dev-alpha",  "label": "alpha",  "new_session_template": "tmux new-session -d -s {name} -c /home/b/dev/alpha",  "delete_session_template": "tmux kill-session -t {name}"}
{"id": "dev-beta",   "label": "beta",   "new_session_template": "tmux new-session -d -s {name} -c /home/b/dev/beta",   "delete_session_template": "tmux kill-session -t {name}"}
...
```

That is a **copy-with-one-edit** problem. It is solved by a Duplicate button and a good paste
target. Neither of those crosses the security boundary.

There is a second reason to prefer duplicate-and-edit over a "working directory" field, and it is
about honesty rather than security: **muxplex cannot know where the path goes in an arbitrary
command.** For `tmux new-session -d -s {name}` it's `-c <dir>`. For `amplifier-workspace {name}`
it might be `cd <dir> && amplifier-workspace {name}`, or a `--workdir` flag, or nothing at all. A
form field labeled "Working directory" on top of an arbitrary template would have to guess, and it
would be wrong for the exact custom commands this feature exists to support. Duplicate-and-edit
never guesses. (The escape from this is a template that declares its own `{cwd}` placeholder — but
declaring it is a file edit, which returns us to the start.)

---

## 4. Options

### Option A — Authoring aid that never writes  ★ recommended (with D)

Settings > Commands becomes a composer. It reads `GET /api/session-commands`, shows every pair
including `default`, and offers **Duplicate** and **Copy** actions that emit a ready-to-run
artifact. It never issues a write of any kind.

| | |
|---|---|
| **User can** | Compose any pair, arbitrary shell, no expressiveness limit. Duplicate an existing pair and change one thing. See existing ids (so collisions are avoided before they happen). Get a one-line command that applies it. |
| **User cannot** | Click Save. The apply step is theirs. |
| **Config lands** | `~/.config/muxplex/settings.json`, written by the user's own shell, via §Option D's CLI (or by hand). |
| **Serves** | **N1 fully** (Duplicate is precisely the "diff paths" shape). **N2 fully** (free text). |
| **Cost** | Frontend only: ~200 lines JS + markup, one `openSettings()` re-fetch, CSS for the composer. Zero Python. Zero API change. |
| **Degrades** | It doesn't. There is no shape the aid can't express, so it never has to lie. The only friction is the paste step, and that friction is the security property made visible. |

The critical design decision inside A: **do not build client-side validation as the authority.**
V1–V7 live in `resolve_session_commands()` (`settings.py:329-440`), and `API_SEMANTICS.md` already
warns against clients re-deriving server-side resolution. Instead:

- Client-side checks are **advisory and minimal** — `{name}` present, id charset, id not already
  taken, id ≠ `default`. Labeled as such on screen: *"checked again by the server when you apply."*
- The **authoritative** verdict is the post-apply refresh: apply, hit Reload, and the errors panel
  shows the server's own strings for what's actually on disk.

That is strictly better than validating a draft, because it validates the artifact that will
actually run. It is also why a `POST /api/session-commands/validate` endpoint is **not** proposed:
it would add API surface, duplicate a check the existing `GET` already performs on real data, and
create a "POST that doesn't write" that a future contributor will eventually be tempted to make
write. `GET /api/session-commands`'s `errors[]` is already the validation endpoint.

### Option B — Closed / parameterized builder

Two sub-variants, evaluated separately because they differ in safety, not just cost.

**B1 — API accepts a free-text working directory.** e.g. `POST /api/sessions {command_id, cwd}`,
shlex-quoted into the template.

**Rejected, and this is the one to read carefully**, because it is the option that looks safest and
isn't. Shell-quoting closes *injection* and nothing else. The residual is not a quoting problem:

> Control over a process's working directory is control over what that process reads.

`git` reads `./.git/config` (`core.pager`, `core.fsmonitor`, `core.sshCommand` all reach
execution). `direnv` reads `./.envrc`. `make`, `npm run`, `mise`/`asdf`, `docker compose` all read
cwd. Even the benign default template ends in a **shell** whose rc hooks fire against the new cwd.
muxplex runs as **root** on `tower`.

The attack needs a second ingredient — a hostile file in a directory the attacker can name — which
a browser download to `~/Downloads` supplies, among others. So this is a *smaller* hole, not *no*
hole. "Smaller hole" is exactly the trade the settled decision refuses. It is materially weaker
than the tmux precedent it would be modeled on, and describing it as "the same shape as
`tmux-config`" would be false.

**B2 — operator pre-declares a path allowlist in the file; the API selects within it.** e.g. a new
`session_command_path_allow` key (file-only, glob patterns), checked at create time.

This one is *architecturally sound* and I want to give it full credit: it is the same shape as
`input_allowed_sessions` (`settings.py:96-97`, `AGENTS.md:58-84`) — a file-only fence expressed as
globs, with the API selecting inside it and the operator's judgment as the control. There is real
precedent in this repo for exactly that pattern.

It still loses, on proportionality:

| | |
|---|---|
| **User can** | Pick a pre-declared directory at create time, without a file edit *per directory*. |
| **User cannot** | Add a directory without a file edit. Do anything for N2. |
| **Config lands** | `settings.json` for the fence; nothing persisted for the selection (it's per-invocation). |
| **Serves** | **N1 partially** (only within pre-declared roots). **N2 not at all.** |
| **Cost** | New `DEFAULT_SETTINGS` key (load-bearing — `save_settings()` drops unknown keys, `settings.py:583-586`), new fence + its `LOCAL_ONLY_KEYS`/`SYNCABLE_KEYS` entries, new field on `POST /api/sessions`, matching field on the federation proxy, a picker UI, `API_SEMANTICS.md` entry, `AGENTS.md` entry, tests. Meaningful Python + docs. |
| **Degrades** | Badly and invisibly: a path outside the fence is a 400 the user has no way to fix from the UI. The dead end moves from "add a pair" to "add a root," and is now further from the thing the user was doing. |

**The sequencing argument is decisive.** N2 requires a file edit no matter what you build. Given
that the user *will* be editing the file, adding a second mechanism that covers part of N1 means
maintaining two configuration surfaces where one would do — and the one you'd still need (the
file) is the one B2 doesn't improve. Build the file path well, and B2 has nothing left to buy.

### Option C — Apply through the live terminal

*"They are, after all, looking at a live terminal in this very app."* Considered, and split:

**C1 — muxplex types the command into the pane for the user. Footgun. Do not build.**

1. It writes into a TTY whose state muxplex cannot know: a vim buffer, a pager, a `sudo` prompt, a
   half-typed line, an agent's REPL. Blind keystroke injection into unknown state is a
   nondeterministic write, and this repo's principle is failing loud, not writing hopefully.
2. It would build a supported product surface on the browser's incidental ability to drive the
   ttyd relay — a path currently guarded only by the active-session claim
   (`API_SEMANTICS.md`, "single shared ttyd process"). Turning a latent exposure into a documented
   feature is the wrong direction, and invites "why can't agents do it too?"
3. It is the exact bypass that `POST /api/sessions/{name}/input` is fenced closed to prevent
   (`AGENTS.md:55-95`). Routing config writes through automated typing defeats that fence with a
   friendlier UI. If this were acceptable, the input fence would not need to exist.

**C2 — hand the user a one-liner and let *them* paste it, anywhere they have a shell — including
the terminal right there. Correct, and it is the apply step of Option A.**

The instinct behind C is right: use the channel where the user already has authority rather than
minting a new one. The error is automating it. A human pressing Enter in their own shell grants
nothing new to anyone. A server typing on their behalf grants everything.

The UI copy should make C2 explicit — *"paste this into any shell, including the terminal in this
app"* — because that is what turns the escape hatch from "go SSH in" into "it's already open."

### Option D — Make the escape hatch one command  ★ recommended (with A)

This is the piece that makes A worth building rather than merely defensible.

**D1 — Stop the lie.** `muxplex config set <fenced-key>` must fail loud:

```
$ muxplex config set session_commands '[...]'
error: 'session_commands' is local-file-only and cannot be set through patch_settings().
       Use:  muxplex commands add ...
       Or edit ~/.config/muxplex/settings.json directly.
```

Non-zero exit. This is a pre-existing bug (documented in `docs/plans/2026-08-02-named-session-command-pairs-plan.md` §0.4 as out of
scope there) that this feature walks users straight into. ~10 lines.

**D2 — `muxplex commands list | add | remove`**, writing via `save_settings()` rather than
`patch_settings()`.

This is not a fence bypass, and the distinction matters enough to state plainly: **the fence exists
to stop remote and Bearer-key callers, not the local operator.** `LOCAL_ONLY_KEYS` is enforced in
`patch_settings()` — the API's write path — and deliberately not in `save_settings()` or
`load_settings()`. A local CLI process invoked by the human is the *intended* writer; hand-editing
the JSON is merely today's clumsiest form of it. Making it a command changes ergonomics, not
authority.

```
muxplex commands add --id dev-alpha --label "alpha" \
  --create 'tmux new-session -d -s {name} -c /home/b/dev/alpha' \
  --delete 'tmux kill-session -t {name}'
```

Runs V1–V7 before writing (one source of truth — call `resolve_session_commands()` on the
prospective list and refuse on any new error). Refuses to clobber an existing id without
`--replace`. Prints what changed. Gets the `settings-history/` snapshot for free, because
`save_settings()` is the choke point (`settings.py:588`).

| | |
|---|---|
| **User can** | Apply a pair with one idempotent, validated, atomic command. No JSON splicing, no stray-comma failure mode. |
| **User cannot** | Do it from the browser. Still, deliberately. |
| **Config lands** | `settings.json`, written by a local process running as the user. |
| **Serves** | The apply half of **both** N1 and N2. |
| **Cost** | ~120 lines in `cli.py` + subparsers, plus tests. No API change, no new settings key. |
| **Degrades** | Cleanly — the file is always still there, and `--dry-run` printing the resulting JSON covers anyone who prefers to edit by hand. |

**One honest risk to record:** `settings.json` has no lock. A CLI write that races a concurrent
server write (federation sync, a PWA `PATCH`) can lose, because both do read-modify-write. This
hazard is pre-existing and applies to every `muxplex config set` today; `commands add` inherits it
rather than creating it. The window is small and `settings-history/` is the recovery path. Worth a
line in the docstring; not worth a locking subsystem in this change.

### Option E — Docs plus a copy-to-clipboard snippet only

The floor, and genuinely close to the recommendation. The delta A adds over E is narrow but real:

- **Existing ids are visible**, so a duplicate-id collision (V7, which rejects *all* copies —
  `settings.py:426-438`) is avoided before it happens rather than diagnosed after.
- **Duplicate is the N1 shape.** A static snippet in a README doesn't prefill from the pair you
  already have working.
- **The errors panel and the composer live in the same place**, so the apply→verify loop closes
  without leaving the screen.

A blank-form composer, by contrast, earns very little over a documented snippet — it is mostly a
fancy textarea. **So scope A tightly: the load-bearing control is Duplicate on an existing pair,
not a from-scratch builder.** That is the proportionate answer to "a pluralized settings field
grew a management surface."

---

## 5. Comparison

| Dimension | A: authoring aid | B1: free-text cwd | B2: declared allowlist | C1: type into pane | D: CLI | E: docs only |
|---|---|---|---|---|---|---|
| **Latency** | n/a — no server work | n/a | n/a | n/a | n/a | n/a |
| **Complexity** | Low. Frontend only, no new concepts | Low code, **high conceptual** — a new safety argument future readers must re-derive | Moderate: new key, new fence, new API field, federation surface | Moderate code, **very high** hidden-state complexity | Low. Follows existing `tmux install` CLI shape | Lowest |
| **Reliability** | High. Nothing to fail; wrong input is caught by the server on refresh | High for the write, **the failure is security not reliability** | High mechanically; 400s the UI can't resolve | **Low.** Outcome depends on unknowable pane state | High. Validated + atomic + snapshotted | High |
| **Cost to build** | ~200 lines JS/CSS | ~40 lines + a security argument that shouldn't be made | ~250 lines across Python, JS, docs, tests | ~150 lines + ongoing risk | ~130 lines CLI + tests | ~20 lines docs |
| **Security** | **Unchanged.** Zero new API verbs, zero new capability | **Weakened.** cwd-as-capability, root on tower | Acceptable — mirrors `input_allowed_sessions` — but new surface to keep correct forever | **Weakened.** Productizes the exact bypass `/input` is fenced against | Unchanged. Local operator authority, already available | Unchanged |
| **Scalability** | Scales with pairs, not code | — | Combinatorial win only inside declared roots | — | Scales fine | Degrades with pair count (manual JSON) |
| **Reversibility** | **Total.** Delete the JS | Poor — an `/api/*` field is a public contract | Poor — new settings key + API field are both contracts | Poor — sets a precedent | High — CLI subcommands are additive | Total |
| **Org fit** | Matches the repo's honest read-only precedent (`app.js:4370-4375`) | Contradicts a settled decision | Consistent pattern, disproportionate for the need | Contradicts `AGENTS.md`'s input fence | Matches `muxplex tmux install`; fixes a live wart | Matches, but leaves the lie in place |
| **Optimizes for** | Honesty + zero security delta | Convenience | Constrained convenience for N1 | Seamlessness | Making the only real path excellent | Doing nothing |
| **Sacrifices** | One paste step | The settled constraint | Simplicity, and N2 still needs the file | Determinism and the input fence | Browser-side apply (permanently) | Ergonomics for N1 |

**Dominant tradeoff:** security-delta vs. paste-step. Everything else is noise. A and D pay a paste
step and buy a zero security delta. B and C buy away the paste step by spending the constraint.

**What would have to be true for A+D to be wrong?** If the user's real need were *per-invocation*
directory choice — "every new session goes in a different, ad-hoc directory I decide at create
time" — then a persisted-pair model is the wrong abstraction and B would be the honest shape,
constraint notwithstanding. The stated need ("I want to use w/ **diff paths**," plural, stable) reads
as a small fixed set of project directories, which is a pair-per-path model. **Worth one question
to the user before building.** If the answer is "no, it's a different directory every time," come
back to B2 — it is the only variant with a defensible safety story, and it would need its own review.

---

## 6. What Settings > Commands becomes

The panel is `data-tab="new-session"` (`index.html:275-296`). Under the recommendation the existing
read-only list **stays, and grows** — the read-only *character* is the point and must not be
softened.

**Six changes, in priority order:**

1. **Always render the list, and include `default`.** Today `app.js:4399` hides the whole field
   when there are no extras, and `app.js:4381` filters `default` out. Both are wrong for
   discoverability: the user with zero pairs — precisely the one who needs to learn the feature
   exists — sees nothing. Always show at least the `default` pair, visually marked as the built-in.
   *One-line fix, largest single win in the document.*

2. **Re-fetch on `openSettings()`.** `loadSessionCommands()` runs once at page init
   (`app.js:5549`) while `loadTmuxConfigSettings()` already re-fetches per open (`app.js:4361`).
   Copy the existing pattern. Without it, "apply, then check" requires a full page reload, and the
   loop that makes Option A work doesn't close.

3. **Per-pair actions: `Duplicate…` and `Copy command`.** Duplicate opens an inline composer
   prefilled from the source pair — id blank (or `-copy` suffixed), label, create, delete, all
   editable. Below it, a live-updating, copyable `muxplex commands add …` line, plus a
   `Show JSON` disclosure for hand-editors. **No Save button exists anywhere in this composer.**

4. **Explain the absence of Save on screen, as capability rather than apology.** Not *"for security
   this can't be changed from the browser"* (the current copy, three times over,
   `index.html:279/284/289`) — that reads as a wall. Something closer to:

   > These run shell commands on the server, so the browser composes them and your shell applies
   > them. Paste this into any shell — including the terminal in this app.
   >
   > `~/.config/muxplex/settings.json` · [copy path]

   Same fact, and it names the door.

5. **Errors: always-visible section header + a badge outside the dialog.** The errors field is
   correct in content (`app.js:4402-4414`) but reachable only by someone who already opened
   Settings > Commands. A malformed pair has two user-visible consequences — it silently vanishes
   from the New Session picker, and any session created with it becomes **undeletable** (409
   `unknown_command_id`, `main.py:~1685`) — and neither points at the cause. Add: a count badge on
   the settings gear and the Commands tab when `errors.length > 0`. That is "fail loud" applied to
   a config error the user otherwise cannot see.

6. **Surface the error at the point of consequence.** When `errors.length > 0`, the New Session
   command picker (`_createCommandSelect()`, `app.js:4732-4756`) should render a non-selectable
   `⚠ N pairs failed to load — see Settings › Commands` row. Today the picker just silently offers
   fewer options, which is indistinguishable from "I never configured it." Same for the delete
   409 path: the message should name the recorded `command_id` and point at the errors panel.

**Explicitly not added:** a `+ New pair` blank-form button. Duplicate covers the real N1 shape and
a blank form earns almost nothing over the documented snippet (§4 Option E). Add it later if the
Duplicate flow proves people want it; adding it now is surface without demand.

---

## 7. The error path, end to end

A user hand-edits a pair and gets it wrong — say, omits `{name}` from the delete template.

| Stage | Behavior today | Under this design |
|---|---|---|
| Server resolve | Entry excluded, error appended (V6, `settings.py:409-415`); logged at startup (`main.py:659-665`) | unchanged |
| `GET /api/session-commands` | `errors: ["session_commands[2] (dev-alpha): 'delete_session_template' must be a non-empty string containing '{name}'"]` | unchanged — the strings are already specific and name the id when one parsed |
| New Session picker | Pair silently absent | `⚠ 1 pair failed to load` row |
| Settings gear | No signal | Count badge |
| Settings > Commands | Error text, if the user thinks to look | Error text + the composer right above it, prefilled, so the fix is one edit and one paste |
| Delete an affected session | 409 `unknown_command_id` | 409 message names the id and points at Settings › Commands |

One residual sharp edge, accepted: an entry that fails **V2** (unparseable `id`) can only be
identified by array index — `session_commands[2]` — because there is no id to name. The user must
count entries in the file. Acceptable; the alternative is echoing the raw entry back, which risks
putting arbitrary file content into UI and log surfaces for marginal gain.

---

## 8. Build order

Each step is independently shippable and independently useful.

1. **`muxplex config set` fails loud on `LOCAL_ONLY_KEYS`** (`cli.py:1561-1592`). Pure bug fix.
   Ship regardless of everything else in this document.
2. **Always render the pairs list, including `default`** (`app.js:4381`, `app.js:4399`). Two lines.
   Biggest discoverability win per byte.
3. **Re-fetch on `openSettings()`** (`app.js:4361` pattern). Closes the apply→verify loop.
4. **`muxplex commands list | add | remove`** (`cli.py`, validating via `resolve_session_commands()`,
   writing via `save_settings()`).
5. **Duplicate composer + copy-command / copy-JSON**, and the reframed helper copy.
6. **Error badge + picker warning row + delete-409 message.**

Steps 1–3 are roughly an hour and already move the needle. Steps 4–6 are the actual feature.

---

## 9. Non-goals, recorded so they don't get re-proposed

- **No API verb that defines a pair.** Settled; this design does not touch it.
- **No `POST /api/session-commands/validate`.** `GET /api/session-commands`'s `errors[]` already
  validates real on-disk state, which is the thing that matters. A write-shaped endpoint that
  doesn't write is an invitation.
- **No client-side reimplementation of V1–V7 as authority.** Advisory only, labeled as such; the
  server's `errors[]` is the verdict. (`API_SEMANTICS.md` makes this point for the `default` fold;
  it applies identically here.)
- **No automated typing into the terminal.** §4 Option C1.
- **No federation of `session_commands`.** Already settled (`API_SEMANTICS.md`); unchanged here.
- **No `settings.json` locking.** Pre-existing hazard, out of scope, documented.
- **No new `DEFAULT_SETTINGS` key.** Nothing in the recommendation needs one — which also means
  nothing here can be silently erased by `save_settings()`'s unknown-key drop
  (`settings.py:583-586`), the trap `docs/plans/2026-08-02-named-session-command-pairs-plan.md` §0.2 recorded.

**API contract impact: none.** No endpoint is added, removed, or changed in shape. muxplex-deck,
federation peers, and agents are unaffected. That is not incidental — it is the strongest available
evidence that this design respects the constraint rather than routing around it.

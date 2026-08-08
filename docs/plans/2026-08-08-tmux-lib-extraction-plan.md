# Extracting a reusable tmux session-management library — where is the seam?

**Status:** design only, not implemented. Nothing here has been built.
Written against `main` at v0.43.0. **Revised same day:** the owner named the
second consumer; §0 is rewritten and §§12–17 added. §§1–8 stand as originally
written and are unmodified.
**Scope of the question:** should muxplex's tmux-facing code become a separate,
reusable Python library, shared by muxplex-the-app and by agents and by future
unrelated projects — and if so, where exactly does the cut go?
**Repos read:** `muxplex/` (server + `client/`), `../muxplex-deck/`.

---

## 0. Verdict, up front — REVISED 2026-08-08

> The original verdict ("not yet as a package — one consumer") stood on §9's
> triggers. Trigger 1 has now fired: the owner named the second consumer — a
> separate application building agent-facing tmux tools **and its own embedded
> human UX** on the same substrate, with improvements required to flow both
> ways ("sharing for DRY"). That is a real second vote for the *local library*,
> not the HTTP client. This section is rewritten; §§1–8 stand as originally
> written; §§12–17 carry the new analysis.

**Internal boundary now — unchanged as step one. The package is now
scheduled, not speculative. The interface freezes only when the second app has
actually pushed on it.**

The honest reading of what changed: this is still **one real consumer plus one
committed intent** — no code in a second repo imports anything today. But the
two-implementation rule governs interface *freezing*, not project *starting*
(§12 argues this in full). What the named consumer changes is that the second
use case now has a *shape* — local app, session lifecycle, presence/restore,
bell detection, fence mechanism, embedded terminal — which is enough to commit
to the extraction path and sequence it, and not enough to settle the interface
decisions where two implementations will disagree (error model, bell-hook
coexistence, observation scoping, ttyd — §15.3).

Two crisp gates replace the old single verdict:

1. **Boundary → package** the moment the second app's repo exists and is ready
   to write its first import. This gate is *earlier* than the original plan
   implied, for a structural reason discovered in §13.1: a separate repo
   **cannot** consume the internal `muxplex/tmux/` boundary — importing it
   means depending on the whole `muxplex` server package (fastapi, uvicorn,
   python-pam, a `muxplex` console script), the exact thing
   `client/pyproject.toml:28-31` forbids for the client. Packaging is a
   **prerequisite** of the second consumer's first line of code, not a
   follow-up to it.
2. **0.x → stable.** The package ships explicitly unstable, co-versioned with
   the muxplex repo (the `client/` precedent), until the second app ships a
   user-visible feature through it and its needs have been reconciled against
   §15's surface. Until then breaking changes are allowed and cheap — both
   consumers are in one owner's hands and share a repo-tag version scheme.

Of the three measured facts that drove the original "not yet":

- *Slowest-moving code in the repo* (fact 1) — *unchanged, and now working in
  favor:* a stable core is exactly what you want under two consumers.
- *The manifest entanglement* (fact 2) — **dissolved**, not by fiat but by a
  consequence of per-app state dirs: §13.3 shows the split is unnecessary once
  each app owns its own single-writer manifest file. This was the highest-risk
  stage in the original plan (§8.3) and it is now off the critical path.
- *The settings inversion buys nothing today* (fact 3) — **inverted:** a
  library whose `run_tmux()` reads `~/.config/muxplex/settings.json`
  (`sessions.py:294`) is unusable by any second app, so stage 3 moves from
  "deferred indefinitely" to "scheduled, gated by the canary discipline"
  (§13.2).

What did NOT change: the seam itself (§2, §3), the shape (§4 — pure library,
no process, no loop), the client relationship (§5 — mirror + contract test,
never a dependency), the safety non-negotiables (never-render rail, presence
discipline, incident tests move with their code — §8.4, §7.3), and the first
step (§7's internal boundary, confirmed in §13.1). New in the addendum: the
bidirectional-flow mechanics that make "improvements flow both ways" true
without a copy step (§14), the concrete import surface a builder can stub
against (§15), the ttyd/embedded-UX seam (§16), and a hazard class that did
not exist with one app — **two apps sharing one tmux server** (§17).

---

## 1. Verification ledger

Every structural claim below was measured against the source, not inferred from
module names.

### 1.1 The import graph

Measured by grepping every `from muxplex...` / `import muxplex...` in
`muxplex/*.py`. This is the whole graph:

| Module | Imports from `muxplex.*` | LOC | Commits (of 789) |
|---|---|---|---|
| `followups.py` | **none** | 270 | 2 |
| `views.py` | **none** | 848 | 9 |
| `state.py` | **none** | 481 | 11 |
| `terminal_input.py` | **none** | 198 | 4 |
| `cgroup_escape.py` | **none** | 246 | — |
| `pruning.py` / `identity.py` / `focus.py` / `breaker.py` / `tls.py` / `tmux_config.py` | **none** | — | — |
| `manifest.py` | `state` (`STATE_DIR` only, `:113`) | 628 | 5 |
| `bells.py` | `sessions` (`run_tmux`), `state` (`empty_bell`) `:22-23` | 261 | 7 |
| `sessions.py` | `cgroup_escape`, **`settings`** `:69-70` | 801 | 16 |
| `ttyd.py` | `cgroup_escape`, `sessions` (`tmux_env`), `state` (`STATE_DIR`) `:86-88` | 865 | 10 |
| `restore.py` | `manifest`, `sessions`, `settings` `:100-111` | 478 | 4 |
| `auth.py` | `settings` | 408 | — |
| `settings.py` | `views` (lazy, `:787`/`:941`) | 1095 | 36 |
| `main.py` | everything | 5283 | 139 |

Two things stand out and both matter:

- **The graph is already almost a layering.** Nine modules import nothing from
  the package at all. The candidate library set is not tangled; it is a set of
  leaves plus one arrow that points the wrong way.
- **That one arrow is `sessions.py → settings.py`.** It is the only import in
  the whole candidate set that would have to be inverted, and it is exactly two
  facts: `tmux_socket_dir` (`sessions.py:294`) and `session_commands`
  (`sessions.py:691-692`). Everything else is already clean.

### 1.2 tmux subprocess call sites

`run_tmux()` (`sessions.py:303`) is a genuine chokepoint. Every tmux invocation
in the package goes through it, from exactly six places outside `sessions.py`:

| Site | Call |
|---|---|
| `bells.py:61` | `list-windows -F #{window_bell_flag}` |
| `main.py:441` | `set-hook -g alert-bell 'run-shell ...'` |
| `main.py:2232-2236` | `send-keys` (the `/input` endpoint) |
| `main.py:2284` | `list-windows` (target-window resolution) |
| `main.py:2573-2575` | `send-keys` (the follow-up queue) |
| `restore.py:322` | `list-windows -F #{window_index}` |

Plus two argv-level spawns in `ttyd.py:440`/`:587` (ttyd itself, not tmux) and
one in `cgroup_escape.py:116` (a `systemd-run` probe).

This is a good sign for extractability: there is one door, not thirty.

### 1.3 What agents actually use today

`muxplex-deck` imports `muxplex_client` and nothing else from this project
(`muxplex-deck/src/muxplex_deck/{main,views,attention,rendering}.py`). The
agent contract in `docs/AGENT_GUIDE.md` is entirely HTTP. `client/pyproject.toml`
explicitly forbids a dependency on the server package.

**No consumer outside this repo imports muxplex Python code today.** That is
the load-bearing fact behind §0.

### 1.4 Corrections to assumptions in the brief

| Assumption | Finding |
|---|---|
| "Two consumers: agents and muxplex's app code" | One *library* consumer. Agents are consumers of the **HTTP API**, not of Python code (§1.3). The library's second consumer is hypothetical. |
| "`manifest.py` is the most transferable idea" | Agreed on the *rule*; the *file* is not. Four of its six top-level keys are muxplex-product (`created_with`, `pending_restore`'s consumer, `rename_in_flight`, and `cwd`'s purpose). §3.1. |
| "the AST scan asserts exactly one `run-shell` construction site" | Confirmed — `test_safety_rails.py:165-217`. It scans `muxplex/*.py` (glob, non-recursive), so **a `muxplex/tmux/` subpackage would fall outside it.** This is a live hazard of the recommended refactor and §7.3 handles it. |

---

## 2. The seam

The cut is not "the files with tmux in the name." It runs *through* four of
them. Adjudicated per concern, from the code:

| Concern | Verdict | Why |
|---|---|---|
| `run_tmux()`, `tmux_env()` argv/env plumbing | **library** | Pure tmux. `sessions.py:264-324`. |
| `SESSION_NAME_RE` / `is_valid_session_name` / `is_tmux_stable_name` | **library** | tmux's own charset and its `.`→`_` mangling. `sessions.py:102-148`. Not muxplex facts. |
| `probe_tmux_epoch()` | **library** | Pure tmux server identity (socket path + inode + pid). `sessions.py:332-391`. |
| `enumerate_sessions()` + the `window_activity` vs `session_activity` finding | **library** | An empirically-established tmux behavior (`sessions.py:46-59`). Every consumer needs it and every consumer would get it wrong. |
| `capture_pane` / `capture_pane_metadata` / `capture_pane_window` + the relative-coordinate conversion | **library** | tmux's `-S`/`-E` are relative and there is no absolute mode; the two-round-trip conversion (`sessions.py:536-565`) is a tmux fact. |
| `update_manifest()` — the presence rule | **library** | Pure function, no I/O, tmux-epoch-shaped. §3.1. |
| `pending_restore` / `created_with` / `renamed_from` / `rename_in_flight` | **app** | Each names a muxplex feature. §3.1. |
| `restore.py`'s fidelity checks | **app** | `_default_workspace_root()` is literally `~/dev` (`restore.py:116-125`); the refusal text cites muxplex docs and `session_commands`. This is the `amplifier-workspace` convention, not a tmux fact. |
| `poll_bell_flag()` — bell **detection** | **library** | `list-windows -F #{window_bell_flag}`, plus the multi-window incident finding (`bells.py:45-56`). Pure tmux. |
| `should_clear_bell()` / `apply_bell_clear_rule()` | **app** | Reads `state["devices"]`, `view_mode == "fullscreen"`, `last_interaction_at`. That is muxplex's UX model. `bells.py:169-192`. |
| the `alert-bell` hook string | **library mechanism, app content** | §3.2. |
| `followups.py` (the pure queue) | **app** (see §3.3) | |
| `session_matches_allowlist()` — glob matching | **library mechanism** | §3.4. |
| `input_enabled` / `input_allowed_sessions` / `LOCAL_ONLY_KEYS` | **app policy** | §3.4. |
| `cgroup_escape.py` | **library** | 100% general, zero muxplex imports, written after the 44-session incident. Any tool that spawns tmux from a systemd unit has this hazard and will not discover it in time. |
| `ttyd.py` | **app** | §3.5. |
| `tmux_config.py` | **app** | Owns `~/.config/muxplex/tmux.d/`, renders muxplex-branded themes, edits the user's `~/.tmux.conf`. The *technique* is general; every constant in it is muxplex's. |
| `views.py`, federation, sync groups, PWA, deck, `auth.py`, `tls.py` | **app** — confirmed, not assumed | Checked: `views.py` imports nothing and knows only about `device_id:name` keys and globs — zero tmux. `auth.py`→`settings` only. `tls.py`→nothing. None of the four touches a tmux subprocess. The brief's boundary holds. |

Approximate result: **~1,200 of 2,494 candidate lines are genuinely general**,
and they are drawn from inside four files rather than as whole files.

---

## 3. The five arguments the brief asked for

### 3.1 Presence and restore

`manifest.py`'s core claim is right and it is general: *a positive presence
record, removed by exactly one thing — observed individual death against a
live, identity-matched server — never by a TTL or a sweep* (`manifest.py:51-55`).
Its three-way discrimination (`:27-49`) is expressed entirely in tmux terms:
epoch same / epoch different / no server. `update_manifest()` (`:241-426`) is a
**pure function** with no I/O; the caller decides whether to persist. That
purity is not incidental — it is what makes this the single safest thing in the
codebase to move (§8.2).

So: `probe_tmux_epoch()` + `update_manifest()` + `compute_restore_plan()` +
`mark_restored()` are library. They are the fix for a class of failure that any
tmux supervisor will eventually hit.

**But the file is not the rule.** `manifest.json` also carries:

- `created_with` — a muxplex `session_commands` id (`settings.py:67`).
- `renamed_from` — set by `main.py:2809`'s rename migration.
- `rename_in_flight` — a write-ahead journal for `POST /api/sessions/{name}/rename`,
  which exists because that endpoint touches one irreversible subprocess plus
  four independently-atomic file writes (`manifest.py:554-568`).
- `cwd` — recorded, per its own docstring, for `restore.py`'s fidelity check
  against the `~/dev/<name>` convention.

None of those four is a tmux concept. And the restore *half* — the thing the
44-session and 52-session incidents actually produced — is muxplex policy end
to end: `_default_workspace_root()` returns `~/dev` (`restore.py:116`), the
refusals name `session_commands` and `docs/API_SEMANTICS.md`, and the whole
module exists as a *CLI* process specifically because running it inside
`muxplex.service` would inherit the cgroup hazard (`restore.py:5-23`).

**The design consequence, and it is the hardest one in this document:** if the
library owns the manifest file, the app's four fields need somewhere to live.
Two options, both bad in a specific way:

- *Opaque `extra` dict on each entry.* Keeps one file, one fsync, one atomic
  write. Costs the type safety and the docstring-as-spec quality that is this
  module's best property, and invites precisely the "hand-edited entry" drift
  the loader defends against (`manifest.py:174-188`).
- *Separate app sidecar file.* Keeps both files typed. Reintroduces
  cross-file-no-transaction — which is the exact problem `rename_in_flight`
  exists to solve, and it would now span a library file and an app file with no
  shared journal.

There is a third option and it is why §0 lands where it does: **do not split
the file at all until a second consumer exists to tell you which fields are
actually common.** Today the answer is unknowable, and guessing wrong here
costs real sessions.

### 3.2 Bells, and the never-render-to-a-pane rule

Two mechanisms, and they belong on opposite sides:

- **Poll detection** (`bells.py:37-66`) is pure tmux, including the finding that
  `display-message -t <session>` reads only the *active* window's flag and
  therefore misses a bell in a background window. Library.
- **The hook** (`main.py:400-460`) registers
  `set-hook -g alert-bell "run-shell '<curl to muxplex's own endpoint>'"`. The
  curl target, the scheme (which only `cli.py` knows), the Bearer key — all
  muxplex. Not library.
- **Attention state** — `unseen_count` / `seen_at` / `source` enum /
  `needs_attention()` / the clear rule gated on a device viewing in fullscreen —
  is muxplex's UX model (`bells.py:169-261`, `state.py:153-173`). App.

**Now the interesting question: does the "never render to a user's pane" rule
belong in the library?**

Yes — and moving it *strengthens* it rather than weakening it.

The hazard is a property of tmux, not of muxplex: `run-shell` paints a
background command's output onto whatever the *client's active pane* is,
independent of which session the command logically belongs to
(`AGENTS.md:461-500`). Any library that manages tmux sessions for agents can
reproduce that incident on someone else's host. The rule travels with the
mechanism.

The enforcement is the subtle part. Today `test_safety_rails.py:165` scans the
production source tree and asserts **exactly one** `run-shell` construction
site, in `main.py`. The right shape after a split is *two* rails, and the pair
is a strictly stronger invariant than the one:

- **Library rail:** exactly one `run-shell` construction site, in the library,
  always silent, with **no parameter that can request a loud variant** — the
  same structural property `_bell_hook_curl()` already has (`AGENTS.md:493-497`).
  The library exposes `build_alert_bell_hook(command: str) -> str`; the caller
  supplies *what to run*, never *how loudly*.
- **App rail:** **zero** `run-shell` construction sites in `muxplex/*.py`.

Today muxplex is allowed one. After the split it would be allowed none — the
one legal construction moves behind an API that cannot be made loud. That is a
tightening, not a relaxation, and it is the single best argument in this
document *for* extraction.

**Hazard to schedule, not to discover later:** the existing rail globs
`muxplex/*.py` — non-recursive. The moment code moves into `muxplex/tmux/`, the
rail silently stops covering it and keeps passing. Whichever option is chosen,
**the rail must be widened to `rglob` in the same commit that creates the
subdirectory.** See §7.3.

### 3.3 Follow-ups

The brief asks whether the follow-up queue is general or muxplex's product.
**Product.** Three independent reasons:

1. `followups.py` is the pure half, and it is the *small* half — 270 lines of
   dict manipulation with no imports. The load-bearing half is
   `_advance_followup_queue()` (`main.py:2513-2606`), which is 94 lines of
   lock discipline, a fire-time settings re-read, a three-way halt
   classification, and a `BaseException` clause that deliberately refuses to
   swallow a fence error into an ordinary halt.
2. Its trigger is a bell **as muxplex defines a bell** — including the rule that
   the poll path may only advance the queue *while the hook is unarmed*, because
   a detached session's bell is observed by both mechanisms and advancing from
   both would drain two items for one physical bell (`bells.py:97-118`). That
   coupling is to muxplex's own dual-detection design, not to tmux.
3. Its fence is `settings.input_enabled` and its storage is `state.json`. Both
   app.

There is a fourth reason that settles it. The follow-up queue is a **published
API feature** — five endpoints, documented in `docs/AGENT_GUIDE.md §6.5`, with
CAS revisions on the wire. Agents consume it over HTTP. Moving it into a local
library would give agents nothing they do not already have and would create a
second implementation of a contract that currently has one.

*What could reasonably move:* nothing, today. If a second consumer ever needs a
durable per-session queue, revisit — but note that the queue's semantics
(halt-on-fence-failure, settle window, peek-send-remove) are tuned to muxplex's
specific bell model and would likely need re-deriving anyway.

### 3.4 The input fence

This is the question that most deserves a real argument, so here is the full one.

**Observation that reframes it:** the fence is not protecting against *local
code*. A process that can `import muxtmux; send_text(...)` can equally
`subprocess.run(["tmux", "send-keys", ...])`. The library adds no capability to
a local process that the OS did not already grant it. What the fence actually
protects is **the network boundary** — the moment a shared Bearer key held by a
remote agent can reach a `send-keys`.

That distinction assigns everything cleanly:

| Piece | Side | Argument |
|---|---|---|
| `build_send_text_argv` / `build_send_key_argv` / `ALLOWED_KEYS` / `MAX_TEXT_BYTES` (`terminal_input.py:41-186`) | **library** | argv construction, `-l` literal mode, `--` end-of-options, the E2BIG bound. All tmux and exec facts. |
| `session_matches_allowlist()` (`:119-165`) | **library mechanism** | The `casefold()` + `fnmatchcase` technique exists because bare `fnmatch.fnmatch` is *platform-dependent* — case-sensitive on Linux, insensitive on macOS. A security fence whose behavior depends on the host OS must never exist, and that is a general truth. |
| `input_allowed_for_session()` (`:90-116`) | **library mechanism** | Fail-closed reads: `is not True`, non-list → empty. Correct anywhere. |
| `input_enabled` / `input_allowed_sessions` **values** | **app policy** | These are muxplex settings, defaulted closed, synced never. |
| `LOCAL_ONLY_KEYS` (`settings.py:238-250`) | **app policy — immovable** | It exists because muxplex has a network API whose Bearer key is *the same credential handed to agents calling `/input`*. A library has no API and no Bearer key. The concept does not translate. |

**So: mechanism in the library, policy in the app.** But mechanism alone is not
enough, because a host that forgets to wire the policy gets an unfenced
`send_text()`. The library must make forgetting impossible:

> The library's send API takes a policy object at construction with **no
> default**. There is no zero-argument way to obtain an object that can type.
> A `DenyAll` policy is the only thing that constructs without an explicit
> decision.

That is deny-by-default and capability-scoping expressed as a type signature —
mechanism that *forces* a policy decision without *making* one.

**One constraint that must survive the move.** `AGENTS.md:19-40` records a
deliberate duplication: `views.matches_name_pattern` and
`terminal_input.session_matches_allowlist` use the identical technique for
opposite reasons — one is a fail-loud display filter, the other is a fail-closed
RCE fence — and must **not** share a mutable implementation. If the library owns
the security matcher, it must own it *as a security matcher*, not as a general
glob utility that a future consumer loosens for display purposes. A library
named `muxtmux.glob_match` invites exactly the merge that rule forbids. Name it
for the fence.

### 3.5 Confirming what is NOT library

The brief asks to confirm rather than assume. Confirmed, with the check:

- **`views.py`** — imports nothing; operates on `device_id:name` keys and glob
  rules; no tmux call anywhere. The destructive-write backstop
  (`views.py:37-61`) is about *settings clobbering over federation*, not tmux.
  **Out.**
- **Federation / sync groups** — `state.py:82-96`, `main.py:4816+`. Entirely
  about multi-device convergence. **Out.**
- **PWA / deck** — the deck consumes HTTP only (§1.3). **Out.**
- **`auth.py` / `tls.py`** — `auth.py` imports only `settings`; `tls.py` imports
  nothing from the package; neither touches tmux. **Out.**
- **`ttyd.py`** — the one genuine judgment call, so it gets an argument rather
  than an assertion. It is *about* tmux (it spawns `tmux attach`), but what it
  manages is a **web terminal server**: per-session UNIX sockets under
  `STATE_DIR/ttyd`, a hashed `mx-<sha>.sock` naming scheme, a relay refcount, an
  idle reaper, an orphan reaper across restarts, and the `SOCKET_SUFFIX` fence
  that stops ttyd silently falling back to `INADDR_ANY:7681` (`ttyd.py:29-43`).
  Every one of those is muxplex's browser-terminal product. Its only tmux
  coupling is `tmux_env()` — one import, which the library would supply.
  **Out**, and the boundary is clean.

---

## 4. Shape, if it is built

### 4.1 Pure library, no process

**The library owns no timer, no task, and no event loop.**

muxplex's 2s poll cycle (`main.py:467-985`) looks like a tmux loop and is not.
Of its fourteen numbered steps, four are tmux (enumerate, epoch, snapshot,
bells); the rest are federation settings sync, device pruning, sync-group GC,
view-key normalization, stale-key pruning with a destructive-write backstop, and
ttyd idle reaping. It is a *server* loop that happens to poll tmux.

What *is* a coherent library operation is the **observation**, as one shot:

```
observe() -> Observation(epoch, names, activity, created, cwds, snapshots?)
```

— idempotent, no persistence decision made, returns a value. muxplex calls it
once per tick from inside its own loop; a CLI calls it once; an agent calls it
on demand. Host owns the schedule, library owns the observation. This is the
mechanism/policy line, and it also happens to be what the code already does:
`enumerate_sessions()` populates caches as a side effect of one tmux call and
`update_manifest()` is a pure function the caller chooses to persist.

### 4.2 Async core, thin sync facade

`sessions.py` is already `asyncio`. muxplex is FastAPI; `restore.py` is driven
by `asyncio.run()` from a CLI; a future agent may be synchronous.

The repo has already solved this exact problem once, well: `client/` puts all
logic in a pure `_protocol.py` and ships `sync_client.py` + `async_client.py` as
thin shells over it, with the duplication confined to the shell "where it is
honest and cheap" (`_protocol.py:1-11`). **Reuse that pattern, not a new one.**

### 4.3 Configuration is injected, never read

The library must not know that `~/.config/muxplex/settings.json` exists. That
means inverting `sessions.py:294` and `:691-692` into an explicit config value
passed at construction — the one non-trivial refactor in the whole plan, and the
reason §8 sequences it late.

### 4.4 Name and boundary

`muxplex/tmux/` today (§7). If it ever ships: a `lib/` member of the existing uv
workspace, alongside `client/`, named for tmux and not for muxplex — because a
package called `muxplex-tmux` that a third project depends on has already lost
the argument about which way the dependency points.

---

## 5. Relationship to `muxplex-client`

They are different things and neither should be built on the other:

|  | `muxplex-client` | the tmux library |
|---|---|---|
| Talks to | a muxplex **server**, over HTTP | a tmux **server**, over subprocess |
| Runs | anywhere on the network | on the host with the sessions |
| Consumers | deck, remote agents | muxplex itself, local tools |
| Dependency | `httpx` only, **never** the server package (`client/pyproject.toml:28-31`) | stdlib only |

**Do not make the client depend on the library** — every deck install would
pull a tmux library it can never use. **Do not make the library depend on the
client** — wrong direction, and it drags `httpx` into a subprocess wrapper.

They should share *vocabulary*, not *code*. There is already a proven mechanism
for that in this repo: `client/muxplex_client/constants.py` **mirrors**
`DEFAULT_CAPTURE_LINES`, `MAX_CAPTURE_LINES`, `ALLOWED_KEYS`, and `MAX_KEYS`
from the server, and `muxplex/tests/test_client_contract.py:31-35` asserts each
mirror equals its original — turning silent drift into a red test *in the PR
that caused it*. A third package joins that arrangement by adding rows to the
same contract test, not by adding an import.

One correction to a tempting idea: the client's dataclasses (`Session`, `Bell`,
`SessionSnapshot`) look like a shared model layer. They are not — they are
*wire* shapes, parsed with `.get()` for version tolerance. The library's types
describe *local* tmux facts. Same nouns, different contracts. Duplicate them.

---

## 6. Options and tradeoffs

| Dimension | **A. Do nothing** | **B. Internal boundary** (`muxplex/tmux/` + import rail) | **C. Extract package now** (workspace member, keep `load_settings()` coupling) | **D. Full extraction** (invert settings, split manifest) |
|---|---|---|---|---|
| **Latency** | n/a | n/a | n/a | n/a — all in-process subprocess work |
| **Complexity** | good — one package | good — one package, one enforced rule | poor — 3 distributions, 3 version numbers, a release order | bad — plus an `extra`-slot or two-file manifest |
| **Reliability** | good — nothing changes | good — pure moves, differential-testable | adequate — same code, new import paths across a release | **poor** — manifest split is the one change that can lose sessions |
| **Cost** | zero | ~1 refactor PR | + packaging, CI matrix, release choreography across 7 hosts | + a wide `run_tmux` call-site diff |
| **Security** | baseline | **better** — app rail goes to zero `run-shell` sites (§3.2) | same as B | same as B, plus a real risk of shipping `send_text()` without a fence unless §3.4's no-default policy holds |
| **Scalability** (of reuse) | none | none *yet* — but conversion is `git mv` | real, for a consumer that does not exist | real |
| **Reversibility** | total | **high** — internal, no published surface | low — a published API is a promise | very low — manifest schema is the least reversible thing here |
| **Org fit** | fits | fits — same repo, same tests, same `make test` DTU | strained — one maintainer, 7 hosts, 68 live sessions per release | poor |
| **Optimizes for** | shipping the current backlog | making the seam real and enforced at zero risk | future reuse | maximal purity |
| **Sacrifices** | the seam stays implicit and will keep eroding into `main.py` | cross-project sharing (unusable today anyway) | fleet stability, for an unnamed consumer | fleet stability *and* the manifest's type safety |

**Dominant tradeoff:** reversibility × reliability against a benefit that is
currently unrealizable. B takes the whole benefit that is realizable today and
pays almost nothing for it.

**What would have to be true for B to be wrong?** That a second consumer exists
now, with known required operations — in which case C becomes correct
immediately, because the interface would have two votes instead of one. See §9.

---

## 7. Recommended: the internal boundary

### 7.1 What moves

Create `muxplex/tmux/` containing only code that passes both tests — *no import
from `muxplex.*` above it*, and *no muxplex-specific constant*:

| New module | From | Contents |
|---|---|---|
| `tmux/proc.py` | `sessions.py:264-324` | `run_tmux()`, `tmux_env(socket_dir)` — **socket dir passed in** |
| `tmux/names.py` | `sessions.py:102-176` | `SESSION_NAME_RE`, `is_valid_session_name`, `is_tmux_stable_name`, `rename_tmux_session` |
| `tmux/observe.py` | `sessions.py:332-491`, `:568-636`, `:780-801` | `probe_tmux_epoch`, `enumerate_sessions`, `capture_pane*`, `snapshot_all` |
| `tmux/presence.py` | `manifest.py:219-489` | `_same_epoch`, `update_manifest`, `compute_restore_plan`, `mark_restored` — **pure, path injected** |
| `tmux/bell.py` | `bells.py:37-66`, `main.py:328-346` | `poll_bell_flag`, `build_alert_bell_hook(command)` (always silent, no loud variant) |
| `tmux/keys.py` | `terminal_input.py` | argv builders, `ALLOWED_KEYS`, caps, `session_matches_allowlist`, `input_allowed_for_session` |
| `tmux/cgroup.py` | `cgroup_escape.py` (whole file) | unchanged |

Everything else stays exactly where it is. In particular: `restore.py`,
`ttyd.py`, `views.py`, `followups.py`, `tmux_config.py`, and all of `main.py`
do **not** move.

### 7.2 The rail

A new test in the style of `test_safety_rails.py`, which already establishes
this idiom (AST scan over the source tree, failing loudly with the incident
attached):

```
for every .py under muxplex/tmux/:
    parse; assert no ImportFrom whose module starts with "muxplex."
        except "muxplex.tmux."
```

This is the entire value of the option. It converts "we intend a boundary" into
"a boundary that cannot erode without a red test," which is the property that
makes a future `git mv` mechanical instead of archaeological.

### 7.3 Two rails that must be fixed in the same commit

Both are live regressions the refactor would otherwise introduce silently:

1. **`test_no_diagnostic_tmux_run_shell_construction_exists`
   (`test_safety_rails.py:177`) uses `package_dir.glob("*.py")` — non-recursive.**
   Code moved into `muxplex/tmux/` leaves its coverage and the test keeps
   passing. Change to `rglob("*.py")`, and change the final assertion from
   `"main.py" in offenders[0]` to the new library site. If §3.2's tightening is
   taken, add the second rail: zero sites in `muxplex/*.py`.
2. **`test_settings_path_is_isolated` and `_isolate_tmux_socket_dir`
   (`test_safety_rails.py:78`, `:89`)** must keep applying to library code that
   still shells out to real tmux. The library's tests inherit the same
   `conftest.py`, which is another argument for staying in-repo.

### 7.4 What this does *not* buy

Stated plainly so it is not oversold: nothing is shared with any other project.
The owner's stated goal — *"re-use in other projects and keep it shared so it
can evolve in all use cases at once"* — is **not delivered** by option B. B
delivers the precondition for it, at a cost low enough to take on a hunch, and
defers the irreversible part until there is something to be right about.

---

## 8. If C or D is chosen anyway: the safe sequence

The fleet is 7 hosts and ~68 live sessions. A tmux session is a live process
tree, not a file (`AGENTS.md:672-680`). The failure mode is **lost work**, not a
red build. Sequence accordingly.

### 8.1 Incremental, never big-bang

Big-bang is not available here. A single release that moves the manifest, the
enumeration, the bell detection, and the settings inversion at once has no
attributable failure: if 68 sessions look wrong afterwards, you cannot tell
which change did it. Every stage below ships and rolls on its own.

| Stage | Change | Fleet risk | Gate to the next stage |
|---|---|---|---|
| 0 | Differential harness (§8.2). No code moves. | none | harness green on recorded real inputs |
| 1 | Move the pure, zero-coupling code (`names`, `keys`, `cgroup`, `presence`'s pure functions). **Re-export from the old module paths** so every existing import and test is untouched. | very low — the diff is "file moved" | full suite green with *zero* test edits |
| 2 | Move `proc` + `observe`. `tmux_env` still reads settings via an injected callable, so behavior is byte-identical. | low | one host for a week, session count unchanged |
| 3 | Invert the settings dependency properly: `run_tmux` takes config; `spawn_session_command` takes a resolved template. Wide diff. | medium | canary per `AGENTS.md`'s cgroup-canary discipline |
| 4 | Split the manifest (app fields out). **Schema change.** | **high** | see §8.3 — do not start without a second consumer |
| — | Publish | — | only after ≥2 consumers exist |

Stage 1 alone is most of the seam. Stages 3 and 4 are where the cost is, and
neither is required for the boundary to be real.

### 8.2 The differential harness — the lever that makes this safe

`update_manifest()` is a **pure function** (`manifest.py:241`). That single
property makes the highest-risk part of the extraction verifiable rather than
hoped-for:

- Record real `(manifest, epoch, live_names, cwds)` tuples from a live host's
  poll cycle for a week — cheap, read-only, no behavior change.
- Replay every tuple through old and new implementations; assert
  `(manifest, changed)` byte-identical.
- Include the four cases the docstring enumerates explicitly, because they are
  the ones that cost sessions: same-epoch tombstone, cold-start freeze,
  `epoch_now is None` no-op, and first-run adoption.

Do the same for `probe_tmux_epoch()` — a pure-ish reader whose output is three
scalars — and for `enumerate_sessions()`'s parser against recorded tmux stdout,
including the malformed-line tolerances at `sessions.py:452-490`.

**If a stage cannot be covered by a differential test, it is not ready to
ship.** That is the whole safety story and it is available because this code was
written pure.

### 8.3 The one thing that can actually lose sessions

Stage 4. If the manifest split gets the presence rule wrong in either direction:

- *Too eager to tombstone* → a live session is removed from the record → a later
  cold start cannot put it in `pending_restore` → the 2026-07-29 failure, where
  44 names survived only by accident.
- *Too reluctant* → stale ghosts accumulate in `pending_restore` → `muxplex
  restore` recreates sessions that were deliberately killed, which is the
  2026-08-05 failure (wrong process, right name, dashboard green).

There is no partial credit and no rollback: sessions do not come back. The
mitigation is not a better test — it is **not doing stage 4 until a second
consumer proves which fields are actually shared.**

### 8.4 What must not break, regardless

- `/api/*` stays byte-identical. `AGENTS.md:3-17` — deck, federation peers, and
  agents are consumers this repo's tests cannot see. `test_client_contract.py`
  is the enforcement and must stay green across every stage.
- The tests that encode incidents move *with* their code: the multi-window bell
  finding, the `window_activity` vs `session_activity` finding, the `.`→`_`
  mangling refusal, the `casefold` + `fnmatchcase` platform argument, the
  `SOCKET_SUFFIX` fallback fence. **A seam that leaves an incident test behind
  is the wrong seam** — if the code moves and its proof does not, the next
  project rediscovers the incident.

---

## 9. What would change the verdict

> **2026-08-08:** Trigger 1 fired — at the level of a named, shaped intent,
> not yet at the level of a written operations list or a second codebase.
> §12 explains why that is enough to commit the path and not enough to freeze
> the interface. This section is retained as the record of what was predicted.

Concrete, checkable triggers. Any one of these flips §0 from B to C:

1. **A named second project**, written down with the list of operations it
   actually needs. Compare that list against §7.1's seven modules. If ≥60%
   overlap, the interface has two votes and should be extracted. If the overlap
   is low, the second project's needs would have *changed* the interface — which
   is precisely the argument for having waited.
2. **An agent that must drive tmux on a host with no muxplex server.** This is
   the only genuinely different consumer shape, because it cannot use HTTP. If
   this appears, extract `observe` + `keys` + `names` first and leave presence
   behind.
3. **A second `run-shell` construction site is proposed for any reason.** The
   two-rail tightening in §3.2 becomes worth the packaging cost on its own.
4. **`muxplex/tmux/`'s import rail fires repeatedly.** If the boundary keeps
   wanting to be violated, it is drawn in the wrong place — that is data, and it
   is data option B produces for free and option A does not.

Absent all four: keep one package, keep the rail, keep shipping the backlog.

---

## 10. Do not build

Restated because each is one "small improvement" away from the design above.

| Rejected | Which decision drifts into it | Why it stays rejected |
|---|---|---|
| A library that owns the poll loop / a background thread | §4.1 "the library owns the observation" | muxplex's loop is a *server* loop; ten of its fourteen steps are not tmux. A library thread would fight the host's scheduler and duplicate `state_lock`. |
| `send_text()` reachable without an explicit policy object | §3.4 mechanism/policy split | A zero-arg constructor that can type is an unfenced RCE surface handed to every future host. `DenyAll` must be the only free default. |
| A general-purpose glob helper shared by the fence and by views | §3.4 "matcher in the library" | `AGENTS.md:19-40`: fail-closed security and fail-loud display must not share a mutable implementation. Name the library function for the fence, not for globbing. |
| `LOCAL_ONLY_KEYS` in the library | §3.4 | It is meaningless without a network API and a shared Bearer key. Moving it would imply a fence a library cannot enforce. |
| Materializing anything into `view["sessions"]` | any touch of view membership during a move | Standing prohibition, load-bearing. `AGENTS.md:19-40`. |
| The library depending on `muxplex-client`, or vice versa | §5 "share vocabulary" | Wrong direction both ways. Mirror + contract test, as `constants.py` already does. |
| Publishing to PyPI before ≥2 consumers | §6 | A published API is a promise; `AGENTS.md` already notes a release that reaches PyPI cannot be unpublished. |
| Splitting the manifest file "while we're in there" | §8.1 stage 1 | Stage 4 is the only change in this plan that can lose real work. It is gated on evidence, not on convenience. |
| Relaxing `test_no_diagnostic_tmux_run_shell_construction_exists` to make the move pass | §7.3 | Widen its scope; never loosen its count. The rail exists because the incident happened twice, in the same file, to the same class of fix. |

---

## 11. Open questions

> **2026-08-08:** partially resolved by the addendum. (1) restore's general
> core — still open; the second app writes its own restore *policy* against
> the lib's `compute_restore_plan` (§15.2). (2) error model — now an
> explicitly deferred interface decision awaiting the second vote (§15.3).
> (3) `cwd` ownership — resolved: lib observes it, app assigns it meaning;
> no split needed (§13.3). (4) version skew — resolved by lockstep repo
> versioning plus per-app single-writer state files (§14.5, §13.3).

These are genuinely unresolved and should not be answered by guessing:

1. **Does `restore.py` have a general core at all?** Its refusal *discipline* —
   refuse with an actionable reason rather than fabricate a command — looks
   general. Its every constant is muxplex's. Undecidable with one consumer.
2. **What is the library's error model?** `run_tmux` raises `RuntimeError` with
   tmux's stderr; `enumerate_sessions` swallows it and returns `[]`;
   `probe_tmux_epoch` returns `None`. Those three disagree deliberately (the
   epoch probe exists *because* enumeration conflates failure with emptiness).
   A published library needs one coherent story, and writing it is real design
   work not yet done.
3. **Does the manifest's `cwd` field survive a split?** It is recorded by a
   library-side observation for an app-side check. Which side owns it depends on
   the answer to (1).
4. **Version skew.** If the library ships separately, a host can run library
   0.3 with muxplex 0.44. The manifest schema is the shared artifact and the
   only thing that matters here — `MANIFEST_SCHEMA_VERSION` (`manifest.py:119`)
   is currently normalized forward-only with nothing branching on it. That is
   fine for one writer; it is not a compatibility story for two.

---

---

# Addendum (2026-08-08): the second consumer is named

The owner's framing, verbatim: *"equip another project that is NOT muxplex to
have the ability to make tools that let agents interact with tmux sessions in
all the ways we do here, and for it to build out its own **integrated** UX for
allowing humans to sometimes interact with an embedded pathway to interact
with their sessions, but not maintain a fully separate version of these core
layers — so if we make improvements there we get them here and vice versa.
Sharing for DRY."*

Sections §12–§17 answer the five questions this raises. Nothing in §§1–8 is
modified; where the addendum supersedes something, it says so explicitly.

## 12. Does this clear the two-implementation bar?

**Honestly: it clears the *commitment* bar, not the *freezing* bar.** The
distinction is the whole answer, so here it is precisely.

What exists today is one real consumer (muxplex) plus one committed, *shaped*
intent. No second codebase imports anything. If the interface were frozen now,
it would be designed against a single concrete implementation and merely
audited against a description of the second — which is the classic way a
"shared" interface turns out to fit exactly one consumer, after which the
second one either contorts or forks, and "sharing for DRY" dies at birth.

But the original verdict did not turn on "two codebases exist." It turned on
"the second use case has no describable shape" (§0-original: *a consumer you
cannot name*). That has genuinely changed. The named shape — a local
application, not an HTTP caller; needing session lifecycle, presence/restore,
bell detection, the typed-input mechanism, and an embedded human terminal
pathway — is sufficient to:

- **rule out the HTTP-client answer** (a second app with its own embedded UX
  cannot be built on `muxplex-client`; it needs the substrate),
- **fix the direction of every §2 adjudication** (all of them survive contact
  with this consumer — checked case by case in §15/§16), and
- **sequence the work** (§13), including committing to the two stages the
  original plan deferred as speculative.

It is *not* sufficient to settle the interface decisions where two real
implementations will disagree — and those are enumerable, not vague: the error
model (§11.2), bell-hook coexistence (§17.1), observation scoping (§17.3),
whether the second app even uses ttyd (§16), and sync-vs-async surface
(§15.3). Each of those is a place where designing from muxplex alone produces
a muxplex-shaped answer.

**Resolution: extract and ship 0.x now-ish (per §13's sequence), freeze
later.** The two-implementation rule is satisfied *in spirit* by keeping the
interface explicitly provisional until the second implementation exists to
vote — which is cheap here, because both consumers belong to one owner and the
package is co-versioned with the repo (§14.5). The single fact that flips 0.x
to stable: **the second app ships a user-visible feature through the library.**
Not "the repo exists," not "it compiles" — a real slice, because only real
usage pushes on interfaces.

## 13. The two-consumer sequence

### 13.1 The internal boundary is still step one — confirmed, with one correction

The instinct in the question is right and the plan's §7/§8 stages 0–2 stand
unchanged: differential harness first, then pure moves with re-exports, then
`proc`/`observe`. Three reasons, none weakened by the second consumer:

1. It is on the critical path of every later step — the package *is* the
   boundary plus a `pyproject.toml`.
2. It is the only place the seam can be proven at zero fleet risk, with the
   differential harness (§8.2) and the rails (§7.3) the package will inherit.
3. It produces seam-violation data (§9 trigger 4) while the interface is still
   free to change.

The correction: the original plan said "if a second consumer ever appears, the
extraction is `git mv`" — implying the boundary could be the *end state* the
second app builds against. **It cannot.** A separate repo importing
`muxplex.tmux` depends on the `muxplex` distribution — fastapi, uvicorn,
python-pam, and a `muxplex` server console script installed on a machine that
may not run muxplex. That is the precise failure `client/pyproject.toml:28-31`
exists to prevent for the client. So the packaging step (§13.2 stage 3.5) is a
**prerequisite of the second app's first import**, and the boundary phase has
a deadline it did not have before: it ends when the second app's repo is ready
to write `from <lib> import ...`.

What the second consumer changes about the sequence, exactly: stages 3 and
3.5 move from *contingent* to *scheduled*, stage 4 *dissolves* (§13.3), and
nothing else moves.

### 13.2 The revised stage table

Supersedes §8.1's table from stage 3 onward. Gates are unchanged in kind:
every stage ships and rolls alone, canary-first, per `AGENTS.md`'s discipline.

| Stage | Change | Status vs original plan | Gate |
|---|---|---|---|
| 0–2 | Harness; pure moves with re-exports; `proc`/`observe` | unchanged | unchanged (§8.1) |
| 3 | Invert the settings dependency: `run_tmux`/`tmux_env` take injected config (`sessions.py:294`); `spawn_session` takes a caller-resolved template (`:691`) | **was deferred-indefinitely; now scheduled** — a lib that reads muxplex's settings file is unusable by the second app | one-host canary, week soak; `tmux_env`'s systemd-environment semantics (`sessions.py:264-300`) proven byte-identical by the harness |
| 3.5 | `git mv muxplex/tmux lib/<name>`; new workspace member beside `client/`; muxplex depends on it as a **runtime** workspace source; version = repo version, 0.x semantics | **new** | **the fleet-install proof (below), before any host rolls** |
| 4 | ~~Split the manifest~~ → replace with the unknown-key round-trip contract | **dissolved** — §13.3 | a test proving app-owned top-level keys survive `update_manifest()` |
| 5 | Second app's first import (git dep, pinned tag) | new | second app's own contract test against its pinned tag |
| 6 | Freeze: 0.x → stable, semver promise | new | second app ships a real feature through the lib; §15.3's open decisions each have a recorded answer |

**The fleet-install proof (stage 3.5's gate).** The seven hosts install
muxplex from this repo. Once the lib is a *runtime* dependency declared as a
workspace source, the install path must resolve the sibling package from the
same checkout. Prove it on one host with the exact command the fleet uses
before rolling anywhere: install from the git URL, import muxplex, run
`muxplex doctor`. If the workspace-source resolution fails under that install
mode, fall back to an explicit
`<lib> @ git+https://github.com/bkrabach/muxplex.git#subdirectory=lib` source
in muxplex's own pyproject — same repo, same commit, same property. The
property being gated is not the mechanism; it is **one repo, one commit, one
rollout** (§14.2). A failure here discovered on host five instead of host one
is a fleet outage.

### 13.3 The manifest question dissolves (supersedes §3.1's dilemma and §8.3's stage 4)

The original plan's hardest problem — split the manifest file and risk the one
change that can lose sessions, or don't extract presence at all — had a third
answer hiding behind a constraint the plan had already adopted. §4.3 requires
every path injected, no defaults. Consequence: **each app has its own state
dir, so each manifest file has exactly one writer.** The two-writers hazard
that made both split options bad (§3.1) does not exist. So: don't split.

The design instead: the library owns the **core keys** — `schema`, `epoch`,
`sessions[*].{first_seen_at, last_seen_at, cwd}`, `pending_restore` — and the
app writes its own keys **beside them in its own file** (for muxplex:
`created_with`, `renamed_from`, `rename_in_flight`). `cwd` stays core,
resolving §11.3: the *observation* is the library's (`#{pane_current_path}`,
one tmux call), the *meaning* is the app's (muxplex's `~/dev` fidelity check;
the second app's whatever-it-decides).

One contract change is required to make this safe, and it is small, testable,
and currently *false*: `update_manifest()` rebuilds the top-level dict from a
closed key set (`manifest.py:335-341`, `:371-377`, `:419-425`), so an
app-owned top-level key would be **dropped on any changed cycle**. In muxplex
today this is unreachable for `rename_in_flight` only because the poll cycle
reads and clears the journal *before* calling `update_manifest()`
(`main.py`, step 1c before step 1b's update) — a call-order accident, not a
contract. The extraction promotes it to a contract: *unknown top-level keys
round-trip verbatim through every library function*, with a test in the
differential harness.

Session-entry level needs no change — the existing behavior is already correct
for this design, and pleasingly so: the same-epoch branch updates entries **in
place** (so an app field like `renamed_from` carries, which is exactly how it
works today, `manifest.py:607-623`), and the cold-start branch freezes old
entries **verbatim** into `pending_restore` (`:390-394`) — preserving app
fields precisely where the app's restore policy will need them — while
rebuilding live entries fresh, which is semantically right (new epoch, new
observation).

Residual risk vs the original stage 4: near zero. No schema migration, no
field moves, no second file, no change to the discrimination rule. The
differential harness covers the one behavioral change (key round-trip) with
recorded real inputs.

## 14. Bidirectional flow: how "improvements flow both ways" actually works

The requirement is the owner's own: a fix made in either app lands in both,
with no copy step. The repo already contains the proven arrangement —
`client/` — and the library copies it with one difference (runtime dep, not
dev-only). Nothing below is novel; that is its virtue.

### 14.1 Layout and declarations

```
muxplex/  (this repo — one uv workspace, one version, one tag)
├── muxplex/     server package — runtime-depends on the lib (workspace source)
├── client/      muxplex-client — httpx only, unchanged
└── lib/         the tmux library — stdlib only
```

- muxplex's pyproject: `dependencies += ["<lib>==<repo version>"]`,
  `[tool.uv.sources] <lib> = { workspace = true }` (with the `#subdirectory=`
  fallback per §13.2's gate).
- The second app's pyproject:
  `<lib> @ git+https://github.com/bkrabach/muxplex.git@v0.44.0#subdirectory=lib`
  — **pinned to a tag**, rolled forward deliberately, exactly as muxplex-deck
  pins `muxplex-client>=0.42.0` today.

Name: not `muxplex-*` (§4.4 — a package the second app depends on must not be
named for the first app), and not `libtmux` (taken on PyPI by tmuxp's
library). Decide at stage 3.5; it is a rename-safe decision until anything
publishes to PyPI, which nothing here does.

### 14.2 Propagation, in both directions

**muxplex → second app:** the improvement lands in this repo — in the *same
PR* as the muxplex change that motivated it, where the lib's own tests, the
differential harness, and muxplex's full suite all run together. Tag. The
second app bumps its pin. Drift window: the time between tag and bump, visible
as a version delta, never as diverged code.

**Second app → muxplex:** a PR against this repo's `lib/` — **never a copy**.
If urgent, the second app pins its own branch ref until the PR merges, then
re-pins the tag. muxplex's fleet picks the fix up on its next ordinary deploy.

**Fleet rollout:** the seven hosts install muxplex from this repo, so a lib
fix rides the exact rollout muxplex already has. There is **no separate lib
rollout to forget** — and that is the drift-prevention property, stated as a
mechanism rather than a policy: muxplex can never observe a lib version it was
not tested against, because they share a commit.

### 14.3 The named anti-pattern

Any step of the form *"copy the lib files into the second app to get moving"*
recreates the fork with extra steps — the two-records-that-drift failure. The
prohibition only has teeth if the git-dep path is genuinely low-friction,
which is why packaging precedes the second app's first commit (§0 gate 1)
rather than following it. If the second app ever contains a file that is
byte-similar to a `lib/` file, that is the incident, regardless of intent.

### 14.4 Drift enforcement — three test layers, all in this repo

1. The lib's own tests, including every incident test that moved with its code
   (§8.4's rule, unchanged and non-negotiable).
2. The differential harness (§8.2), retained after extraction as the lib's
   regression bed for the presence rule.
3. A muxplex-side contract test in the style of `test_client_contract.py:31-35`
   for anything muxplex mirrors rather than imports. The second app adds its
   own contract test against the tag it pins, in its repo.

### 14.5 Versioning, and the honesty clause

Lockstep with the repo tag pre-stable — the `client/` precedent (0.43.0 =
0.43.0). Independent semver only if the lib ever moves to its own repo, which
nothing in this plan requires or recommends.

The honesty clause: "improvements flow both ways" holds only while both
consumers track close to head. A second app that pins a six-month-old tag has
recreated the fork with a pin instead of a copy. Cheap governance: a weekly
non-blocking canary job in the second app's CI that builds against `main`'s
lib, so drift is visible before it is expensive.

## 15. The public surface

### 15.1 What the second app imports — concrete enough to stub against

```python
from <lib> import (
    # -- process / config (all argv-exec; config injected, never read) -----
    TmuxTarget,            # socket_dir + env policy; replaces the
                           # load_settings() read inside tmux_env()
    run_tmux,              # async; raises TmuxError carrying tmux's stderr
    spawn_session,         # (name, resolved_template, target) — cgroup-escaped,
                           # with the exists-after-nonzero-exit tolerance
                           # (the TTY-attach case, sessions.py:674-683);
                           # template resolution stays in the app

    # -- names (tmux facts, security-load-bearing) --------------------------
    SESSION_NAME_RE, is_valid_session_name, is_tmux_stable_name,
    rename_session,        # exact-match =target + `--`, rc-0-can-still-mangle
                           # caveat carried in the docstring

    # -- observation ---------------------------------------------------------
    observe,               # (target, *, scope=None) -> Observation(
                           #   epoch, names, activity, created, cwds)
                           # one shot, no persistence decision; see §17.3 for
                           # why `scope` is first-class
    probe_epoch,           # -> Epoch(socket_path, server_pid, inode) | None
    capture_pane, capture_pane_metadata, capture_pane_window, snapshot_all,
    DEFAULT_CAPTURE_LINES, MAX_CAPTURE_LINES,

    # -- presence (pure core + injected-path I/O) ----------------------------
    load_manifest, save_manifest,     # path REQUIRED, no default (§13.3)
    update_manifest,                  # pure; round-trips unknown keys (§13.3)
    compute_restore_plan, mark_restored,

    # -- bells (detection only; attention semantics are the app's) ----------
    poll_bell_flag,                   # all windows, not the active one
    build_alert_bell_hook,            # (command) -> hook string; ALWAYS
                                      # silent; no loud variant exists (§3.2)

    # -- typed input (mechanism; policy REQUIRED) ----------------------------
    SendPolicy, DenyAll, AllowSessionsMatching,   # casefold+fnmatchcase;
                                                  # named for the fence, not
                                                  # for globbing (§3.4)
    Sender,                           # Sender(target, policy) — no
                                      # zero-policy constructor exists
    ALLOWED_KEYS, MAX_TEXT_BYTES, MAX_KEYS,

    # -- cgroup safety (the 44-session incident, packaged) -------------------
    should_escape, wrap_exec_argv, wrap_shell_argv,
)
```

That list is §7.1's seven modules plus `spawn_session` — which the original
plan left app-side because it read settings; with the template
caller-resolved (stage 3), the general half (cgroup-escaped spawn,
exists-despite-exit-code tolerance) is exactly what a second app creating
sessions needs and must not rediscover.

### 15.2 What stays muxplex-private — restated as the second app's checklist

Views, federation, sync groups, the PWA, the deck, auth modes, TLS,
`tmux_config.py`, follow-ups-as-product (the queue endpoints and
`_advance_followup_queue`'s halt semantics), `LOCAL_ONLY_KEYS`, the restore
*policy* (`restore.py` — the `~/dev` convention, the refusal texts, the
`--force` semantics), and the ttyd **relay** (§16). The second app is expected
to write its own: fence policy values, restore policy against
`compute_restore_plan` + its own fidelity rules, attention/UX model over
`poll_bell_flag` + its own state, and its own schedule for `observe()`.

### 15.3 Deliberately open until the second vote

These are the interface decisions where two implementations will disagree,
listed so nobody "helpfully" settles them from muxplex's side alone:

1. **Error model** (§11.2): `run_tmux` raises; `enumerate_sessions` swallows
   to `[]`; `probe_epoch` returns `None`. The disagreement is deliberate
   *inside muxplex* (the epoch probe exists because enumeration conflates
   failure with emptiness); a published surface needs the story told once.
2. **Bell-hook coexistence** (§17.1) — the sharpest one.
3. **Observation scoping** (§17.3): `scope` as parameter vs caller filtering.
4. **ttyd lifecycle** (§16): second tranche, shaped by the second app's UX.
5. **Sync facade**: muxplex is async-only; add the `_protocol.py`-style sync
   shell (§4.2) only if the second app is actually synchronous. Do not
   pre-build it.

## 16. The embedded-UX seam: ttyd

§3.5 called `ttyd.py` "app" with one judgment-call paragraph. The second
app's *"embedded pathway for humans to interact with their sessions"* forces
the finer cut, and on re-reading, most of the file is honestly substrate:

**Shareable (second tranche):** the per-session-ttyd-over-AF_UNIX lifecycle —
`socket_path_for`'s hashed fixed-width naming, `validate_socket_dir`'s
symlink/WSL/ownership/sun-path/bind-probe checks, the `SUN_PATH_BUDGET=102`
portability constant, `socket_is_live` (a real `connect()`, never
`Path.exists()`), `spawn_ttyd`'s readiness gate, `ensure_ttyd`/`kill_ttyd`,
identity-checked orphan reaping across restarts, relay refcounting and idle
reaping — and above all the **`SOCKET_SUFFIX` fence**: a non-`.sock` path
makes ttyd log a warning and silently fall back to listening on
**TCP `INADDR_ANY:7681`** with exit status 0 (`ttyd.py:29-43`) — the exact
exposure the original `-i 127.0.0.1` incident was about, on a different port.
A second app that embeds ttyd without this fence *will* ship an
unauthenticated writable terminal onto someone's LAN. These are ttyd facts
and incident-derived safety, not muxplex product, and the file's only
in-package imports are already the lib's (`tmux_env`, `STATE_DIR`,
`cgroup_escape` — `ttyd.py:86-88`), so the move is cheap *when it happens*.

**muxplex-private, permanently:** the **relay** — `terminal_ws_proxy`
(`main.py:4149`) with `_ws_auth_check`, the Bearer-only input-fence gate
(`main.py:4363`), takeover semantics, and the federation variant
(`main.py:4494`); the `terminal_session`/`terminal_group` bookkeeping in
`state.py`; `reap_legacy_ttyd` and the `TTYD_PORT` legacy wire field (a
muxplex migration); and the PWA frontend. The relay is where auth and the
fence live, and §3.4's own argument makes that app by construction.

**The seam, in one sentence:** *the library hands the app a live AF_UNIX
socket speaking ttyd's web-terminal protocol for session X — and structurally
guarantees it can never silently become a TCP listener; everything between
that socket and a human's eyeballs — transport, auth, fence policy, frontend —
is the app.*

**Why second tranche and not day one:** the second app may not want ttyd at
all — its "integrated UX" could be its own PTY layer, an xterm.js+node-pty
stack, a TUI, or an SSH pathway. Moving ttyd lifecycle before that UX design
exists would freeze the least-validated interface in the whole set, against
one implementation, on the piece with the sharpest security fence. The
forcing function is the second app's embedded-terminal design doc; until it
exists, `ttyd.py` stays where it is. When it moves, its incident tests move
with it (§8.4's rule): the SOCKET_SUFFIX fence tests, the socket-liveness
tests, the sun-path budget tests.

## 17. New hazard class: two apps, one tmux server

This did not exist with one consumer and nobody has had to think about it
before: the tmux server is a **shared singleton**, and some of its state is a
single global slot. Every hazard below fails *silently*, which is the worst
failure shape in this codebase's recorded history. None blocks the
extraction; all must be named in the lib's docs before a second app ships on
a host that also runs muxplex.

1. **The `alert-bell` hook slot.** `set-hook -g` overwrites — and muxplex's
   arming *depends* on exactly that for its retry-safety ("simply overwrites
   whatever hook is already set, so calling this repeatedly is always safe,"
   `main.py:403-406`). Two apps arming independently: last writer wins and
   the other app's hook-path bells go dark with no error anywhere. tmux hooks
   are array options, so indexed coexistence is *technically* expressible,
   but it trades away the overwrite-idempotency that makes arming safe to
   retry, and un-arming your own index without disturbing a neighbor's is
   untested ground. **This is §15.3's interface decision #2**, and the v0
   posture should be the boring one: (a) at most one app arms the hook on a
   given tmux server; others run poll-only detection — which the lib ships
   anyway, because the hook already has a known blind spot the poll covers
   (`bells.py:81-85`); plus (b) a doctor-style check that reads the current
   hook back and warns loudly when it is not yours. Indexed coexistence only
   if a real deployment ever needs two hook consumers on one server.
2. **Fence overlap.** Each app's `SendPolicy` is its own; nothing stops two
   apps' policies both matching one session, and by §3.4's own argument
   nothing should — the fence protects each app's *network* boundary, not the
   pane. But the operator-facing consequence must be documented: "session S
   is un-typeable" now requires checking every app's policy, not one
   settings.json. On a shared host, partition by session-name prefix per app;
   the glob mechanism already expresses this.
3. **Presence cross-talk.** Each app's manifest observes *all* sessions —
   including the other app's. App B's cold start would freeze app A's
   sessions into *its* `pending_restore`, and app B's restore policy would
   then offer to recreate sessions it does not understand — the 2026-08-05
   incident shape (wrong process, right name, dashboard green) reproduced
   *across an app boundary*. The mechanism to prevent it exists today
   (`update_manifest` takes `live_names` from the caller), but as a caller
   convention it will be forgotten. This is why `observe(scope=...)` is a
   first-class parameter in §15.1 rather than a documentation note: a
   forgotten filter here is silent and its failure mode costs real sessions.
   muxplex itself passes no scope (it owns the whole server today — 68 names
   predate any partitioning rule); a second app on a shared host must.
4. **Session-name collisions.** One flat namespace; two apps creating
   `build` collide at creation time (tmux refuses duplicates, so this one at
   least fails loudly). Prefix-partitioning again; the lib may carry a
   naming-convention convenience but must not require one.
5. **Non-hazards, for completeness:** enumeration and capture are read-only
   and concurrency-safe; cgroup escape is per-spawn and conflict-free;
   per-app state dirs (§13.3) keep every file single-writer; and each
   codebase keeps its own never-render rail (library: exactly one silent
   `run-shell` construction site; each app: zero — §3.2's tightening applies
   to the second app on day one, not just to muxplex).

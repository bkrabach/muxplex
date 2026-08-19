# Embedded-agent availability: how a user actually ends up with a working agent panel

**Status:** design, not implemented. Tracked as work item `muxplex-x60`.
**Scope:** how `amplifier-agent` gets onto a user's machine, and what happens when
it cannot. NOT the runtime call model — that is already settled and correct (§1.1).
**Prerequisite for Phase 2 only:** `amplifier-agent` (and `amplifier-core` /
`amplifier-foundation` / `amplifier`) published to PyPI. Tracked separately under the
`amplifier` project. Phase 1 has no cross-team dependency and ships now.
**Related:** `muxplex-lf6` (self-upgrade re-exec hardening) — §9.

---

## 0. Read first — three facts that decide the shape of this

### 0.1 The runtime is already a library. Only the *install* is a hack.

The obvious framing — "stop shelling out to the agent, embed it as a library" — is
already done and shipped. `agent_embedded/runner.py:108-110` imports
`amplifier_agent_lib` directly, `runner.py:181` and `:216` import
`amplifier_agent_cli`, and the turn runs **inside the uvicorn process** behind
`main.py:6030` (`POST /api/agent/chat/completions`). The old sidecar subprocess is
gone. There is a clean degrade path already: `check_available()`
(`runner.py:170-186`) returns a human-readable reason and the route answers **503**
with it (`main.py:6061-6066`) rather than opening a stream that dies.

So there is no runtime work in this design. **The entire problem is the install
strategy and the Python floor**, and that is where every option below is aimed.

### 0.2 `ensure_agent()` exists because PyPI forbids the dependency we actually want

`cli.py:1122` `ensure_agent()` runs, at runtime, on the user's machine:

```
uv tool install --reinstall --refresh --force <muxplex-target> \
  --with 'amplifier-agent @ git+https://github.com/microsoft/amplifier-agent@v0.12.0'
```
(`cli.py:1250-1261`; the pin comes from this install's own metadata via
`_agent_target_pin()` → `_declared_dependency_pin()`, `cli.py:786-791` / `cli.py:437`,
backstopped by `_AGENT_FALLBACK_PIN = "0.12.0"` at `cli.py:783`.)

This is not an accident of taste. The module note at `cli.py:734-771` and
`service_install()`'s docstring (`service.py:430-438`) both state the reason: **a
PyPI-published package cannot carry a direct-URL (git) dependency** — PyPI rejects
direct references in `Requires-Dist` — and `[tool.uv.sources]` (`pyproject.toml:173`)
is uv-dev-only and **never enters a published wheel's metadata**. The identical
restriction is documented for tmux-kit in `AGENTS.md`'s "tmux-kit pin/tag agreement"
section and was verified against a real built wheel's `METADATA` at v0.45.1.

`amplifier-agent` is git-only — not on PyPI, at any version. So today:

> Neither a PyPI `uv tool install muxplex` nor a plain git `uv tool install
> muxplex` gets `amplifier-agent` on its own. `ensure_agent()` is the only thing
> that closes that gap.

**"Just declare it as a dependency" is the right answer and is currently
unavailable.** It becomes available the moment `amplifier-agent` is on PyPI, and not
one day sooner. That is what makes this a two-phase design rather than a one-line
`pyproject.toml` edit.

### 0.3 The 3.11 failure is a *missing marker*, not a missing package

`pyproject.toml:25` sets muxplex's floor at `requires-python = ">=3.11"`.
`amplifier-agent` requires `>=3.12` — at **every** released version, v0.9.0 through
v0.13.0 (`amplifier-agent/pyproject.toml:4`). The declarative `agent` extra already
handles this correctly with a marker (`pyproject.toml:85-87`, rationale in the
comment block at `:77-84`):

```toml
agent = [
    "amplifier-agent==0.12.0; python_version>='3.12'",
]
```

`ensure_agent()`'s **imperative** `--with` string carries no such marker. On Python
3.11 the resolver is handed a requirement it cannot satisfy under any circumstances
and the user gets a raw uv resolution dump —
`amplifier-agent==0.12.0 depends on Python>=3.12 ... unsatisfiable`. A real user is
on 3.11.14 and hit exactly this.

**The honest limit, stated once and never softened elsewhere in this document:** no
version of `amplifier-agent` supports Python 3.11. A 3.11 user **cannot** get the
agent panel by any mechanism in this design. The best achievable outcome for them is
a clear sentence telling them why and what to do — instead of a resolver traceback.
Phase 1 delivers exactly that and nothing more, deliberately.

---

## 1. Current state

| Concern | Where | Behaviour today |
|---|---|---|
| Runtime call model | `agent_embedded/runner.py:108-224`, route `main.py:6030` | **In-process library.** Correct; unchanged by this design |
| Unavailable degrade | `runner.py:90` `EmbeddedAgentUnavailable`, `runner.py:170-186` `check_available()`, `main.py:6061-6066` | Clean 503 with a reason. Correct; unchanged |
| Declared dependency | `pyproject.toml:85-87` (`agent` extra) + `:173` (`[tool.uv.sources]`) | Marker-guarded, git-sourced — **only reachable from a git checkout** (`uv sync --extra agent`) |
| Install on a user's box | `cli.py:1122` `ensure_agent()`, install at `:1250-1261` | Runtime `uv tool install --with 'git+…'`. No marker. Raw traceback on 3.11 |
| Provider modules | `cli.py:1058-1119` `_run_agent_post_install()` | `load_and_prepare_bundle(install_deps=True)` — a real, separate step (§7.2) |
| How a user is told | `cli.py:1845-1862` (`doctor`) | `⚠ amplifier-agent — not installed` → `Run: muxplex ensure-agent`. **Manual.** Same wording on 3.11, where the command cannot succeed |
| Automatic call sites | `cli.py:2771` (`upgrade`), `service.py:439` (`service install`) | Already automatic on those two paths |
| Manual command | `cli.py:3993-3995` (`ensure-agent`) | The documented escape hatch |
| Pin agreement | `muxplex/tests/test_amplifier_agent_pin_source_agreement.py` | Asserts **three** copies agree: the extra's `==X.Y.Z`, the uv source's `tag="vX.Y.Z"`, and `cli._AGENT_FALLBACK_PIN`; plus source-is-git-not-`path`, and pin-is-`==`-not-a-URL |

Three user-visible defects fall out of that table:

1. **Manual step.** A fresh `uv tool install muxplex` has a dead panel until the user
   reads `doctor` and runs a second command.
2. **`doctor` nags identically on 3.11**, where the command it recommends cannot
   possibly work.
3. **Raw traceback on 3.11** instead of an explanation (§0.3).

---

## 2. Options considered

| # | Option | Verdict |
|---|---|---|
| A | Keep the runtime install; make it automatic + Python-floor-aware + non-fatal | **CHOSEN as the interim (Phase 1)** |
| B | Hard dependency, raise muxplex's floor to 3.12 | **REJECTED** |
| C | Marker-conditional declared dependency, once `amplifier-agent` is on PyPI | **CHOSEN as the end state (Phase 2)** |
| D | Vendor `amplifier-agent` into muxplex | **REJECTED** |

**Why B is rejected.** It "solves" the 3.11 problem by deleting 3.11 users. muxplex's
core value — a tmux dashboard — has nothing to do with the agent panel and works
perfectly on 3.11 today; the classifier list (`pyproject.toml:16`), the CI matrix
(`ci.yml:22`), and `muxplex-client`'s own 3.11–3.13 support all reflect that.
Dropping a whole interpreter version so that an *optional* capability can be a
*required* dependency is a trade in the wrong direction, and it converts a degraded
panel into an uninstallable product for the one user we actually know about.

**Why D is rejected.** Vendoring buys install-time availability at the cost of owning
someone else's release train: `amplifier-agent` pulls `fastapi`, `uvicorn`,
`pydantic`, `mcp`, and `amplifier-foundation` (itself git-sourced,
`pyproject.toml:178`), and its provider modules are resolved from its own bundle at
load time (§7.2) — which vendoring does not eliminate. It also does not fix the 3.11
case: vendored code that requires 3.12 still requires 3.12. Strictly worse than C on
every axis, and worse than A today.

**Why C is not shippable yet.** §0.2. It is blocked on a PyPI publish that lives in
another repo. That is precisely why A exists.

---

## 3. Decision

**Two phases, and muxplex keeps its 3.11 floor in both.**

- **Phase 1 (now, no cross-team dependency):** keep `ensure_agent()`, but make it
  (a) automatic on the managed paths, (b) Python-floor-aware — mirroring the same
  `python_version>='3.12'` guard the declarative extra already uses — and
  (c) non-fatal, so muxplex core is untouched when the agent cannot be installed.
- **Phase 2 (after the PyPI publish prerequisite):** declare `amplifier-agent` as a
  **marker-conditional base dependency**, delete the runtime install hack.

```toml
# Phase 2, [project].dependencies
"amplifier-agent>=X.Y.Z ; python_version>='3.12'",
```

On 3.12+ that is pulled automatically by `uv tool install muxplex` — no
`ensure-agent`, no `doctor` nag. On 3.11 the marker simply skips it: **muxplex still
installs and still runs; only the panel is absent**, which is exactly the state the
503 degrade path (`main.py:6061-6066`) was built to render.

The rationale is one sentence: *the agent panel is an optional capability, so its
absence must cost the user a feature, never an install.*

---

## 4. Phase 1 — the interim (ships now)

Three changes, all inside the existing `ensure_agent` / `doctor` surface. No new
commands, no new config keys, no new module.

### 4.1 One floor predicate, checked before anything else runs

Add a single helper next to `_agent_target_pin()` (`cli.py:786-791`) — call it
`_agent_python_supported()` — and have **every** entry point consult it first:
`ensure_agent()` (`cli.py:1122`), `doctor`'s agent block (`cli.py:1845-1862`), and
the `ensure-agent` dispatch (`cli.py:3993-3995`).

It must encode the **same** constraint as the declarative extra
(`pyproject.toml:86`), not a second hand-written one. The floor lives in exactly one
place in this repo and everything else reads it. A test asserts the constant and the
marker agree (§8.1) — same discipline as the existing pin-agreement test, for the
same reason: two copies of a version constraint silently diverge.

### 4.2 On an unsupported interpreter: explain, skip, exit 0

Below the floor, `ensure_agent()` prints and returns **without invoking uv at all** —
the resolver is never given a requirement it cannot satisfy, so there is no traceback
to suppress:

```
  ! amplifier-agent skipped — the embedded agent panel requires Python >=3.12
    (this muxplex is running on Python 3.11.14).
    muxplex itself is unaffected; everything except the agent panel works.
    To enable the panel, reinstall muxplex on a newer interpreter:
      uv tool install --python 3.12 --force muxplex
```

**Exit status is 0, not 1.** This is a correctly-reported unsupported configuration,
not a failure — `ensure_agent()`'s two automatic call sites (`cli.py:2771` in
`upgrade`, `service.py:439` in `service install`) already treat a `False` return as
non-fatal, but returning failure for a state the user cannot change turns every
upgrade into a scary-looking one. `muxplex ensure-agent` run by hand on 3.11 should
print the explanation and succeed.

### 4.3 `doctor` says the same thing, in the same voice

`doctor`'s agent block (`cli.py:1845-1862`) gains the same guard. Below the floor it
must **not** print `Run: muxplex ensure-agent` — recommending a command that cannot
work is the specific friction this item exists to remove. It prints the §4.2
explanation instead, as a warning (never a failure mark — the existing comment at
`cli.py:1837-1844` already establishes that an absent agent is a warning).

Above the floor, `doctor`'s behaviour is unchanged in Phase 1.

### 4.4 "Automatic" means the managed paths, and they already are

`upgrade` (`cli.py:2771`) and `service install` (`service.py:439`) already call
`ensure_agent()` unconditionally. Phase 1 does not add new automatic call sites —
**it makes the existing ones safe on 3.11**, which is what actually changes the
user's experience. The remaining manual case is a bare `uv tool install muxplex`
with no `service install`, and that gap closes properly in Phase 2 rather than by
bolting an installer onto server startup.

> **Do NOT** call `ensure_agent()` from `serve()` or from the app lifespan. A
> network-reaching, venv-rewriting `uv tool install` on the request path is a far
> worse failure mode than a missing panel, and `check_available()` already renders
> the missing panel honestly.

### 4.5 Why this reaches the affected user

The 3.11 user is running `muxplex ensure-agent` and `muxplex doctor` **on their
installed version**. Phase 1 ships in a normal muxplex release; their next
`muxplex update` picks it up, and the very next `doctor` tells them the truth
instead of nagging, and the very next `ensure-agent` explains instead of dumping a
resolver trace. No coordination with any other repo is required for that to land.

---

## 5. Phase 2 — the end state (after the PyPI prerequisite)

Blocked on `amplifier-agent` being published to PyPI (tracked under the `amplifier`
project — see §9.1). When it is:

1. **Move the dependency from the `agent` extra into base `[project].dependencies`**,
   keeping the marker:
   `"amplifier-agent>=X.Y.Z ; python_version>='3.12'"`.
   A plain version specifier is legal in `Requires-Dist`; a git URL is not (§0.2).
   This is the whole unlock.
2. **Delete `[tool.uv.sources]`'s `amplifier-agent` entry** (`pyproject.toml:169-173`)
   and, if `amplifier-foundation` is likewise on PyPI by then, its entry too
   (`:174-178`). Run `uv lock` **in the same commit** — `AGENTS.md`'s tmux-kit section
   documents why a source edit without a relock leaves git installs resolving from a
   stale lock.
3. **Delete `ensure_agent()`'s install path** (`cli.py:1250-1261`) and its call sites
   (`cli.py:2771`, `service.py:439`). §7.3 covers what survives.
4. **`doctor` changes register**: on 3.12+ a missing `amplifier-agent` stops being a
   "run this command" nag and becomes a genuine anomaly (a broken install), because
   the dependency resolver should have provided it. On 3.11 it keeps the §4.2
   explanation permanently — that text is not interim, it is the terminal state for
   that interpreter.
5. **Keep the `agent` extra as an alias** for one release cycle so
   `uv sync --extra agent` in a git checkout and any documented
   `muxplex[agent]` invocation do not break; then remove it. An extra that resolves
   to an already-required package is a no-op, not a second copy.

---

## 6. Version policy: automated frequent bumps, gated on our own CI

**Track `amplifier-agent`'s latest via an automated dependency bump (Renovate or
Dependabot) that opens a PR per upstream release and runs muxplex's full CI against
it before anyone merges.** Each accepted bump ships in a normal muxplex release.

**Do not float unpinned, and do not branch-track.** Both are rejected for the same
two reasons:

1. **Non-reproducible installs.** Two users running `uv tool install muxplex` a week
   apart would get different agent code from the identical muxplex version. That is
   the exact class of silent divergence `AGENTS.md`'s tmux-kit section exists to
   prevent — and it is worse here, because there would be no pin to compare against.
2. **Unbounded blast radius.** A bad upstream release would reach every muxplex user
   with no muxplex change and no muxplex CI run in between. A bump PR puts our own
   test suite between an upstream release and our users, which is the entire value.

### 6.1 Reworking the pin-agreement test

`test_amplifier_agent_pin_source_agreement.py` currently asserts an **exact-pin**
invariant across three copies (§1). Automated bumps and `==` do not compose — every
bot PR would have to edit three files in lockstep, and the third copy
(`cli._AGENT_FALLBACK_PIN`) exists only to serve `ensure_agent()`, which Phase 2
deletes.

Rework in step with the phases:

| Phase | What the test should assert |
|---|---|
| 1 | Unchanged (`==` across all three), **plus** the new §4.1 floor constant agrees with the `agent` extra's `python_version>='3.12'` marker |
| 2 | Floor/range check: the base dependency is a `>=` (or `>=,<`) specifier, carries the marker, is **not** a direct URL, and the floor constant still agrees with the marker. Drop the `[tool.uv.sources]` tag assertion and the `_AGENT_FALLBACK_PIN` assertion **only when** the source entry and the constant are actually deleted — never before |

Keep the two assertions that are about *shape*, not version, in both phases: the
dependency must never become a `git+` URL (PyPI would reject the release), and any
`[tool.uv.sources]` entry that still exists must be `git`, never `path` — a leftover
cross-repo dev-loop override.

---

## 7. Back-compat and migration

### 7.1 Nobody's muxplex breaks

| Situation | Phase 1 | Phase 2 |
|---|---|---|
| Python 3.11 user, no agent | `doctor` explains instead of nagging; `ensure-agent` explains instead of tracebacking; **muxplex core unaffected** | Marker skips the dependency; muxplex installs and runs; panel absent, 503 with a reason |
| Python 3.12+ user, agent already installed | Unchanged | Unchanged; the resolver now owns the version |
| Python 3.12+ user, fresh install | Still needs `service install` / `upgrade` / `ensure-agent` | **Automatic.** No second command |
| Git checkout (`uv sync --extra agent`) | Unchanged | Extra becomes a no-op alias, then is removed (§5.5) |
| Panel while the agent is missing | 503 + reason (`main.py:6061-6066`) | Identical. This path is load-bearing in both phases and must not be removed |

### 7.2 The provider bundle-prepare step stays runtime, in both phases

`amplifier-agent`'s panel-selectable provider modules (anthropic, openai, azure,
ollama, github-copilot) are **bundle-declared**, resolved at load time by
`load_and_prepare_bundle(install_deps=True)` — that is what
`_run_agent_post_install()` (`cli.py:1058-1119`) drives. Declaring the *library* as
a dependency does not declare *those*; nothing in `pyproject.toml` can.

So a one-time, first-use preparation step is **inherent**, not a leftover of the hack.
What Phase 2 changes is that it can be **automatic on first use** — triggered from
the same place that already knows the panel is being asked for — rather than a
command the user has to be told to run. State this plainly in the docs; do not let
"it's a declared dependency now" be read as "there is nothing left to prepare."

### 7.3 What survives the deletion of the runtime install

`ensure_agent()`'s install invocation goes. Its *diagnostics* should not: the import
probe (`_agent_import_probe()`, `cli.py:794+`) and the provider-importability checks
are what let `doctor` say something true about a broken environment. Phase 2 keeps
those as read-only checks and deletes only the code that mutates the venv.

---

## 8. Test plan

Run inside a DTU: `make test`. Commit first — `git archive HEAD` is what the DTU
tests (`AGENTS.md`). Never on a host running a live muxplex.

### 8.1 Unit — the floor guard (Phase 1)

- `_agent_python_supported()` is `False` on 3.11, `True` on 3.12 and 3.13.
- **The floor constant and the `agent` extra's marker name the same version.** Parse
  `pyproject.toml:86`'s marker; assert against the constant. This is the assertion
  that fails if someone bumps one and not the other — same failure mode, and same
  remedy, as the existing pin-agreement test.
- With the interpreter faked below the floor, `ensure_agent()` **never constructs the
  uv command** — assert on the subprocess mock being uncalled, not merely on the exit
  code. The point is that uv is never handed an unsatisfiable requirement.
- `ensure_agent()` returns success (exit 0) below the floor, and its message contains
  the interpreter version and a concrete reinstall command.

### 8.2 Unit — `doctor` wording (Phase 1)

- Below the floor: output contains the explanation and **does not** contain
  `Run: muxplex ensure-agent`. Assert the absence explicitly — the nag is the defect.
- Above the floor, agent absent: unchanged from today (warning + `ensure-agent`).
- Above the floor, agent present: unchanged, including the version-mismatch branch
  (`cli.py:1856-1862`).

### 8.3 The 3.11 path end-to-end

The CI matrix already gives us a real 3.11 interpreter (`ci.yml:22`) and already
installs `--extra agent` (`ci.yml:35-48`), where the marker makes it a no-op on the
3.11 leg — `test_agent_credential_embedded.py:72`'s `needs_amplifier_agent_cli` guard
skips cleanly there. That existing arrangement is the fixture: on the 3.11 leg,
assert `muxplex ensure-agent` exits 0, prints the explanation, and its output
contains no `Traceback` and no `unsatisfiable`.

### 8.4 Phase 2

- `pyproject.toml`'s base dependency carries the marker and is not a direct URL
  (§6.1) — this is the assertion that keeps the release publishable.
- A built wheel's `METADATA` actually contains
  `Requires-Dist: amplifier-agent>=X; python_version >= "3.12"`. Inspect the real
  wheel — that is how the tmux-kit equivalent was established, and inference is not
  good enough here.
- `grep -rn "uv tool install" muxplex/cli.py` shows no agent `--with` path remaining.
- The 503 degrade path still renders on a 3.11 install (§7.1).

---

## 9. Related work and sequencing

### 9.1 Prerequisite: PyPI publication (the `amplifier` project)

Phase 2 cannot start until `amplifier-agent` is on PyPI (with
`amplifier-core` / `amplifier-foundation` / `amplifier` as needed for its own
resolution — `amplifier-foundation` is currently git-sourced even transitively,
`pyproject.toml:174-178`). **That work is not designed here.** It is tracked under the
`amplifier` project and is a hard gate: until the package resolves from a registry,
step 1 of §5 is a release that PyPI will reject.

Phase 1 is deliberately built to need none of it.

### 9.2 `muxplex-lf6` — self-upgrade re-exec hardening

`muxplex-lf6` fixes the upgrade flow running post-install steps in the **stale**
interpreter: `upgrade()` overwrites muxplex on disk and then keeps executing in the
old process, so a first-time lazy import of a *new* module resolves cross-module
names against the *cached old* one. That is the `ImportError: cannot import name
'ensure_agent' from 'muxplex.cli'` a user hit via `service.py:439`.

**Coordination, both directions:**

- Phase 2 of this design **deletes** the `ensure_agent()` call from the post-install
  path (`cli.py:2771`, `service.py:439`), removing that particular trigger.
- **That is not a fix for lf6 and must not be treated as one.** The stale-interpreter
  trap is general: it fires for *any* post-install cross-module import, and the
  post-install path keeps doing other work (service-file regeneration, restart). lf6
  must land on its own merits, on its own schedule.
- Ordering is free — neither blocks the other. If lf6 lands first, Phase 2 is a
  smaller diff on a path that is already safe. If Phase 2 lands first, lf6's diff is
  unchanged.

---

## 10. Out of scope — do not build as part of this

- Backporting `amplifier-agent` to Python 3.11, or negotiating for it. Not ours, and
  §0.3 is the honest limit.
- Raising muxplex's own floor to 3.12 (option B, §2 — **rejected**, not deferred).
- Vendoring (option D — **rejected**).
- Auto-installing the agent from `serve()` or the app lifespan (§4.4).
- Multi-provider selection in the embedded runner (`runner.py:123+` `active_provider()`
  is single-provider by design today; a separate design).
- Anything about *credentials* — that is `docs/designs/agent-credentials.md`'s
  territory and is orthogonal: this design decides whether the library is present,
  that one decides whether it has a key.
- `CHANGELOG.md` / version bump: **no.** Release-time, owner-only (`AGENTS.md`).

---

## 11. Open questions

1. **Floor specifier in Phase 2: `>=X` or `>=X,<Y`?** An open upper bound plus
   automated bumps means a user installing today can resolve an agent version our CI
   has never seen. A capped range makes every upstream release require a muxplex
   release to reach users — safer, slower, and it re-creates part of the pin problem.
   Recommendation: **start capped** (`>=X,<X+1`) while the bump automation proves
   itself, then loosen. Owner call.
2. **Which bot?** Renovate handles Python markers and grouped bumps better than
   Dependabot; Dependabot is zero-setup on GitHub. No strong technical forcing
   function — pick one and configure it to run the full CI matrix, not a subset.
3. **Does `amplifier-foundation` reach PyPI in the same batch?** If it lags,
   `amplifier-agent` on PyPI still cannot resolve for a PyPI muxplex user, and
   Phase 2 stays blocked on the *transitive* git source
   (`pyproject.toml:174-178`) even though the direct one is fixed. Confirm the
   `amplifier`-project item covers the whole tree, not just the leaf.
4. **Should the §4.2 message name a specific reinstall command?**
   `uv tool install --python 3.12 --force muxplex` is right for a uv-tool install and
   wrong for pipx or a system Python. Suggested resolution: reuse
   `_get_install_info()`'s existing provenance detection (already used throughout
   `doctor`) to tailor the line, and fall back to a generic sentence when the source
   is unknown — never print a command that will fail for this user.

---

## 12. Documentation to update

- **`README.md`** — the agent panel's install story. Phase 1: the panel needs Python
  ≥3.12, stated up front rather than discovered at `ensure-agent` time. Phase 2:
  delete the `ensure-agent` step entirely for 3.12+, and describe the one-time
  first-use provider preparation (§7.2) so its absence from `pyproject.toml` is not
  a surprise.
- **`AGENTS.md`** — a short section, in the same place as the tmux-kit pin/tag rule:
  the amplifier-agent Python floor lives in exactly one constant and is asserted
  against the marker; the dependency must never become a `git+` URL in
  `[project].dependencies` (PyPI rejects the release); and, in Phase 2, that the
  runtime `uv tool install --with` path is gone and must not come back.
- **`docs/AGENT_GUIDE.md`** — one paragraph on what a 3.11 user sees and why.
- **`docs/designs/agent-credentials.md`** — a cross-reference only. Its §11
  assumption 2 pins `amplifier-agent` at v0.12.0; §6's version policy invalidates
  "stays at v0.12.0" as a standing assumption and replaces it with "moves often, and
  our CI is the gate."

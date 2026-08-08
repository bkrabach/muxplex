# tmuxkit to its own repo + PyPI — restore public installs, keep the git path

**Status:** design only, not implemented. Written against muxplex `main` at
v0.44.0 (lib/ shipped as a workspace member; muxplex 0.44.0 and
muxplex-client 0.44.0 on PyPI; muxplex's PyPI wheel currently
install-broken by its unresolvable `tmuxkit==0.44.0` pin).
**Sequel to:** `2026-08-08-tmux-lib-extraction-plan.md` — this is the §14.5
"independent semver / own repo" step that plan named possible but did not
recommend. The owner has now decided it. This plan designs it honestly,
costs included.
**Scope:** packaging and distribution only. muxplex's `/api/*` contract and
all runtime behavior are unchanged. tmuxkit's code is unchanged — it moves.

---

## 0. Three things to say before the design

### 0.1 URGENT, independent of everything else: the `tmuxkit` name is unclaimed on PyPI

`https://pypi.org/simple/tmuxkit/` returns **404 — the name is unregistered**
(verified 2026-08-08). Meanwhile muxplex 0.44.0's **published PyPI wheel**
declares `Requires-Dist: tmuxkit==0.44.0`. Today that makes public installs
*fail*. The day a stranger registers `tmuxkit` on PyPI with a `0.44.0`
release, public installs **succeed — running the stranger's code.** That is
a textbook dependency-confusion window, open right now, against a package
that spawns subprocesses and manages terminals.

**Consequence for sequencing:** publishing tmuxkit to PyPI is not just the
fix for the broken install — it is the *close* of a live security window.
It goes first (step S1), before the repo split is even finished if
necessary (the first upload can be made from the muxplex repo's existing
tree; §10). Do not let repo-creation ceremony delay the name claim.

### 0.2 The honest alternative the owner should see once before paying for the split

If the *only* goal were "make `uv tool install muxplex` work from PyPI
again," **no repo split is needed.** Add one build line to the existing
`publish.yml` (`uv build --package tmuxkit`) plus one trusted publisher on
PyPI, and tmuxkit publishes from the monorepo — keeping the extraction
plan's headline property intact: *one repo, one commit, one rollout, drift
impossible by construction.*

The split is justified by the goals the monorepo option does **not**
serve: a public library identity (its own README, issues, and PRs for
third-party consumers — the second app's contributors should not have to
navigate a personal server repo whose `AGENTS.md` is fleet-operational
incident detail), a release cadence decoupled from muxplex's, and the §14.5
"own semver line" the extraction plan reserved for exactly this moment.
Those are real, the owner has decided them, and this plan proceeds — but
§9 prices what they cost, because the cost is the thing the extraction
plan bragged about giving us.

### 0.3 The load-bearing mechanism, verified — and half of it turns out unnecessary

The hinge question was whether one wheel can serve both worlds. Findings
(§2 has the evidence):

- **(a) `[tool.uv.sources]` never enters wheel metadata — TRUE by
  construction**, not by uv version. `Requires-Dist` is derived from
  `[project.dependencies]` only; `[tool.*]` tables are not part of core
  metadata. uv's own docs: sources are "only respected by uv … only the
  definitions in the standard project tables" are used by anything else.
  PyPI's rejection is of direct URLs *in `Requires-Dist`* ("Can't have
  direct dependency" — warehouse's own error); a plain `tmuxkit>=0.44.0`
  spec sails through. This half is safe to build on.
- **(b) whether `uv tool install git+…/muxplex` honors muxplex's own
  `[tool.uv.sources]` git entry — UNPROVEN and uv-version-dependent.** The
  docs frame sources as development-time; uv demonstrably ignores sources
  of *transitive* dependencies (astral-sh/uv #11388). The shipped state
  proves only the `{ workspace = true }` variant works on the fleet's uv
  versions — a *different code path* (workspace discovery inside one
  checkout) from a cross-repo `{ git = … }` source. **Do not bet the
  design on (b).**
- **The reframing that dissolves the bet:** once tmuxkit is *on PyPI*, the
  git-install path does not need a source entry at all. `uv tool install
  git+…/muxplex` has *always* resolved fastapi, uvicorn, and httpx from an
  index — the git path was never a no-index path. After S1, tmuxkit is
  just one more index-resolved dependency, reaching CISO devices through
  whatever index/mirror already serves fastapi. The dual-declaration
  mechanism is therefore **not the design; it is an optional hardening**
  for one specific org posture (§3.2), with a uv-version-independent
  fallback (`--with 'tmuxkit @ git+…'`) if (b) is false when tested.

So the recommended muxplex `pyproject.toml` after the split is the boring
one: `tmuxkit==<version>` in `[project.dependencies]`, **no
`[tool.uv.sources]` entry for tmuxkit at all.** One declaration, both
install paths, nothing version-dependent. §2.3 gives the fallback ladder
for the stricter CISO posture.

---

## 1. Verification ledger

Every load-bearing claim, checked — with what was verified vs assumed.

| # | Claim | Status | Evidence |
|---|---|---|---|
| 1 | PyPI rejects direct-URL deps in `Requires-Dist` | **Verified** | Warehouse error `Invalid value for requires_dist … Can't have direct dependency` (pypa/pip#6301, pypa/twine#726, pypi/warehouse#9404) |
| 2 | `[tool.uv.sources]` never enters wheel `METADATA` | **True by construction** | Core metadata `Requires-Dist` maps from `[project.dependencies]` (PEP 621); `[tool.*]` is tool-scoped. uv docs: "Sources are only respected by uv… only the definitions in the standard project tables will be used" |
| 3 | The **sdist** contains `pyproject.toml` verbatim, sources included — and PyPI still accepts it | True by construction | PyPI validates `PKG-INFO` metadata, not tool tables. (muxplex 0.44.0's sdist with `muxplex-client = { workspace = true }` is already on PyPI — the precedent is live) |
| 4 | uv honors a project's *own* `tool.uv.sources` when that project is installed **from git** | **UNPROVEN** for `{ git = … }` sources | Docs frame sources as development-time; #11388 shows sources ignored for non-root packages. Only `{ workspace = true }` inside the same checkout is fleet-proven. Treated as a gated *optional* mechanism (§2.3), never the primary path |
| 5 | `tmuxkit` unregistered on PyPI | **Verified 2026-08-08** | `pypi.org/simple/tmuxkit/` → 404. See §0.1 hazard |
| 6 | tmuxkit is stdlib-only — zero transitive index fetches on any path | **Verified** | `lib/pyproject.toml`: `dependencies = []`, with the stay-empty contract comment; enforced by `test_lib_import_smoke.py` and the AST import-purity rail |
| 7 | `uv tool install tmuxkit` as a proof gate | **WRONG — corrected** | tmuxkit has no `[project.scripts]`; `uv tool install` refuses packages with no executables. The standalone gate is `uv add tmuxkit` + import smoke (§8, G3) |
| 8 | Trusted Publishing precedent exists in-house | Verified | muxplex's `publish.yml` already uses OIDC (`id-token: write`, `environment: pypi`, `pypa/gh-action-pypi-publish`), with a comment documenting the 403-on-missing-publisher failure mode |
| 9 | Old fleet tags keep working during the transition | True by construction | git tags are immutable; `uv tool install git+…/muxplex@v0.44.0` still sees `lib/` in that tag's tree after `lib/` is deleted on `main` |
| 10 | Publishing tmuxkit 0.44.0 **retroactively repairs** the already-published muxplex 0.44.0 wheel | True by resolution semantics | The wheel's `tmuxkit==0.44.0` starts resolving the moment that version exists on PyPI — no muxplex release needed for the public path (§4) |

## 2. The mechanics, resolved

### 2.1 What the published muxplex wheel declares

`[project.dependencies]` carries `tmuxkit==<pinned version>` — a plain
version specifier. That is what enters `Requires-Dist`, what PyPI accepts,
and what public `uv tool install muxplex` resolves from PyPI. Nothing else
is available to a wheel: claim (a) is settled.

### 2.2 What the git install resolves

`uv tool install git+https://github.com/bkrabach/muxplex@vX` builds muxplex
from the checkout and resolves its dependencies — fastapi, uvicorn, …, and
now tmuxkit — from the configured index, exactly as it already does for
every non-workspace dependency today. **No `[tool.uv.sources]` entry is
required, so none is added.** This removes the uv-version dependency from
the critical path entirely: both install paths run on standards
metadata.

### 2.3 The stricter CISO posture, and the fallback ladder

If (and only if — confirm with the actual policy, §11) the managed-device
requirement is *"first-party packages must come from vetted git, not from
an index"*, three mechanisms, in order of preference:

1. **Install-time override (recommended; uv-version-independent):**
   ```
   uv tool install git+https://github.com/bkrabach/muxplex@v0.45.0 \
     --with 'tmuxkit @ git+https://github.com/bkrabach/tmux-kit@v0.44.0'
   ```
   A direct-URL requirement at the *operation* level pins tmuxkit's source
   regardless of how uv treats project sources. Verify post-install via
   the tool venv's `tmuxkit-*.dist-info/direct_url.json`. This is one
   documented line in the CISO install runbook — no repo mechanism at all.
2. **Project-level git source (`[tool.uv.sources] tmuxkit = { git = …,
   tag = … }`)** — only if gate **G2b** (§8) proves the fleet's uv version
   honors it on a git install of muxplex. If proven, it makes the override
   unnecessary; if not, it is dead weight and is not merged.
3. **Offline wheelhouse** (`uv export` + `--find-links`) — the only true
   *no-index* mechanism, needed only if a device genuinely cannot reach
   any index. Out of scope here (such a device cannot install fastapi
   today either); named so nobody mistakes the git path for it.

### 2.4 The CISO story, stated honestly

The earlier "PyPI is blocked on managed devices" finding cannot mean "no
index at all": the git path those devices use today already pulls fastapi,
uvicorn, cryptography, and friends from an index or mirror. The honest
reading is *"muxplex-the-package must be installed from vetted git"* (with
ordinary deps flowing through PyPI or an org mirror). Under that reading,
tmuxkit-on-PyPI (or on the same mirror, once vetted like fastapi was)
needs nothing special. Under the stricter first-party-from-git reading,
§2.3's ladder applies. **Pre-work item for the owner: confirm which
reading is the actual policy** — one sentence from the device policy
decides whether mechanism 1 goes in the runbook or nothing is needed.

And the transitive question: tmuxkit's `dependencies = []` (verified,
enforced by test) — a git-sourced tmuxkit adds **zero** index fetches.

## 3. Repo creation and history

### 3.1 `bkrabach/tmux-kit`, public, via `git subtree split`

**Decision: subtree split of `lib/`, not a clean start, not a deep
filter-repo rewrite.**

```
# in the muxplex repo
git subtree split --prefix=lib -b tmuxkit-export
# in the new, empty bkrabach/tmux-kit
git pull <muxplex-repo> tmuxkit-export
```

- *Why not clean start:* the lib-era commits (extraction stages, the
  round-trip contract fix, rail changes) are the design record of the code
  as it exists; they are small, recent, and free to keep.
- *Why not filter-repo with path renames to carry pre-extraction history:*
  the `git mv` from `muxplex/*.py` into `lib/tmuxkit/` means the deep
  history lives under paths outside the prefix; carrying it requires a
  full rewrite that interleaves unrelated server history — cost without
  benefit, because **the incident rationale lives in docstrings and
  tests, which travel as file content**, and the muxplex repo remains the
  permanent archaeological record (it is not going anywhere). The new
  repo's README gets a one-line pre-history pointer:
  *"History before 2026-08-08 lives in bkrabach/muxplex (extracted per
  docs/plans/2026-08-08-tmux-lib-extraction-plan.md)."*

Layout in the new repo: `pyproject.toml` + `tmuxkit/` at the root (drop
the `lib/` nesting — it was workspace scaffolding), `tests/`, `README.md`,
`CONSUMERS.md` (already written to travel), `.github/workflows/`.

### 3.2 What must travel with the code — non-negotiable

Per the extraction plan's §8.4 rule (*a seam that strands an incident test
is wrong*), the split moves, not copies:

- **All tmuxkit unit + incident tests** currently in `muxplex/tests/`:
  presence-rule tests, the multi-window bell finding, the `.`→`_` mangling
  refusal, casefold+fnmatchcase platform tests, cgroup-escape tests, the
  stdlib-only import smoke.
- **The differential harness and its recordings**
  (`test_differential_harness.py`, `-m differential`). The recordings are
  fleet-captured data; they are the regression bed for the presence rule
  and they belong with the function they test.
- **The library half of the never-render rail:** *exactly one `run-shell`
  construction site in `tmuxkit/**`, in `bell.py`, with no parameter that
  can request a loud variant* — an AST scan in tmux-kit's own suite.
- **The safety-rail conftest fixtures the integration tests depend on:**
  isolated `TMUX_TMPDIR` (autouse), `TMUX` unset, never a bare
  `kill-server` — tmux-kit's new `tests/conftest.py` reimplements these
  (they are small), because its integration tests run a real tmux and the
  rails are what has kept real tmux servers alive through this suite.
- **The import-purity rail** becomes tmux-kit's simplest test: no module
  under `tmuxkit/` imports anything outside stdlib + `tmuxkit.*`.

### 3.3 What stays in muxplex — and the one new cross-repo test

- Tests of muxplex's *use* of tmuxkit (poll-cycle integration, restore
  integration, bell-hook arming, everything driving `muxplex.main`).
- muxplex's never-render rail **tightens to zero**: no `run-shell`
  construction site anywhere in `muxplex/**` (the extraction's §3.2
  two-rail scheme, now split across two repos).
- **New: `test_tmuxkit_contract.py`** — the cross-repo drift tripwire,
  modeled on `test_client_contract.py`. Runs against the *installed*
  tmuxkit at the pinned version and asserts: mirrored constants equal
  (`DEFAULT_CAPTURE_LINES`, `MAX_CAPTURE_LINES`, `ALLOWED_KEYS`,
  `MAX_KEYS`); `build_alert_bell_hook`'s signature has no loudness
  parameter (introspection — the no-loud-variant property, checkable from
  outside); `import tmuxkit` drags in no fastapi/httpx/pam
  (`sys.modules` smoke); presence round-trips an unknown top-level key.
  This is what turns a bad pin bump red in the muxplex PR that made it.
- `test_safety_rails.py::test_library_tests_live_under_the_railed_tests_dir`
  is **retired** in the same PR that deletes `lib/` — its premise ends.
  Retire by replacement (point it at the new contract test's existence),
  never by silent deletion; the rail file's own module docstring demands
  that discipline.

## 4. Versioning — a one-way door, decided

**tmux-kit's first PyPI release is `0.44.0`, built from a tree verified
identical to `muxplex@v0.44.0:lib/`.** Verification is mechanical, not
visual: `git rev-parse v0.44.0:lib` in muxplex must equal the tree hash of
the new repo's `v0.44.0` tag content (modulo the `lib/` → root move —
compare the `lib/` subtree hash against the new root tree after the
layout change is committed *separately* from any content change).

Why 0.44.0 and not a clean 0.1.0:

1. **It repairs the already-published muxplex 0.44.0 wheel retroactively
   and instantly.** That wheel pins `tmuxkit==0.44.0`; the moment PyPI has
   that version, public `uv tool install muxplex` starts working — zero
   muxplex release, zero fleet action. A 0.1.0 reset leaves muxplex
   0.44.0 on PyPI *permanently* broken (releases cannot be unpublished;
   yanking it is the only remedy and helps nobody).
2. **It closes the §0.1 confusion window with the exact version an
   attacker would target.**
3. The "cleaner public story" of 0.1.0 buys nothing real: 0.44.0 is
   honest provenance (44 lockstep releases of history), and the README
   says so in one line.

After 0.44.0, the lines **decouple**: tmux-kit advances on its own 0.x
cadence (still no semver promise until the extraction plan's stage-6
freeze — that gate is unchanged by the split); muxplex bumps its pin
deliberately. muxplex 0.45.0 (the release that drops `lib/`) keeps
`tmuxkit==0.44.0` — byte-identical code, now resolved from PyPI, which
makes the packaging change and any behavior change impossible to conflate.

**Pin policy: exact `==`, not a range.** muxplex is an application;
applications pin. Under 0.x, a range like `>=0.44,<0.45` is a fiction
(0.x minor bumps may break), and an open range would let a fleet host
resolve a tmuxkit that no muxplex test ever ran against — recreating
silently the drift the monorepo made impossible. The `==` pin makes drift
*visible as a version delta* and makes "tested together" a property of
every muxplex release. Its cost is the two-step dance, priced in §9.

## 5. muxplex repo changes (lands as v0.45.0)

Ordered within one PR, after tmux-kit is live and 0.44.0 is on PyPI:

1. `pyproject.toml`: remove `"lib"` from `[tool.uv.workspace] members`;
   delete `tmuxkit = { workspace = true }` from `[tool.uv.sources]`; keep
   `tmuxkit==0.44.0` in dependencies with the comment rewritten to name
   the new repo and the pin-bump discipline. **No git source entry**
   (§2.3 — only if G2b proves it and the CISO posture demands it).
2. Delete `lib/` (the split already carried it out). Tombstone in
   `CHANGELOG.md`, not a stub directory.
3. `uv lock` — tmuxkit now resolves from PyPI into the lockfile with a
   registry source and hash.
4. `publish.yml`: no change needed (it already builds only
   `muxplex` + `muxplex-client` by explicit `--package` flags — the
   comment that anticipated exactly this future stays accurate). The
   long comment explaining why tmuxkit is *not* built here gets updated
   to point at the new repo.
5. Tests per §3.3: move the lib tests out, add `test_tmuxkit_contract.py`,
   tighten the never-render rail to zero, retire the
   lib-tests-location rail.
6. **New CI guard:** assert the committed `pyproject.toml` has no `path`
   or `git` source for tmuxkit. Reason: the cross-repo dev loop (below)
   works by *temporarily* adding one, and the predictable failure is
   committing it — which would silently turn every git install into a
   moving-target resolve and break `uv build` reproducibility.

**The cross-repo dev loop, documented (it gets worse; say so):** working
on both at once is no longer "edit two directories in one repo." The
workflow: `uv add --editable ../tmux-kit` (writes a temporary path
source), develop, then *revert the pyproject change* before committing —
the CI guard in (6) catches the miss. Single-repo tmuxkit work needs
nothing special.

**CI ordering hazard:** muxplex's CI cannot go green on this PR until
tmuxkit 0.44.0 is actually resolvable from PyPI. Strict order: S1–S3
before S4 (§10). Note `test-latest-deps`-style jobs that install with
bare pip/uv against PyPI now *also* exercise the tmuxkit resolution —
which is a feature: that job becomes a standing G1 regression check.

## 6. CI + publishing on `bkrabach/tmux-kit`

### 6.1 `test.yml`

- Matrix: Python 3.11 / 3.12 / 3.13 (mirrors both packages' declared
  support); `ubuntu-latest` with `apt-get install tmux`, plus one
  `macos-latest` job with `brew install tmux` — the sun-path budget and
  `#{pane_current_path}` behaviors are platform-sensitive and the library
  claims macOS support.
- Runs the **full** suite including `-m integration` (real tmux against
  an isolated `TMUX_TMPDIR`, per the traveled conftest rails) and
  `-m differential`. In the muxplex repo the integration marker is
  deselected by default because the suite is dangerous near a live
  muxplex; the tmux-kit repo's CI runners have no live muxplex, so
  integration runs unconditionally there. Keep the local-run guard
  (isolated socket dir assert) anyway — contributors have live tmux.

### 6.2 `publish.yml` — Trusted Publishing (OIDC), no long-lived token

A near-copy of muxplex's existing workflow (which is already the OIDC
shape): trigger on `v*` tags, `permissions: id-token: write`,
`environment: pypi`, `uv build` (no `--package` needed — single-project
repo, no workspace), then `pypa/gh-action-pypi-publish@release/v1`.
Recommended over an API token for the same reasons muxplex already made
this choice: nothing to rotate, nothing to leak, scope bound to one
repo+workflow+environment triple.

### 6.3 What the owner must configure by hand (the agent cannot)

**PyPI web UI — pending publisher** (Account → Publishing → "Add a new
pending publisher", because the project does not exist yet; a *pending*
publisher both reserves the name binding and authorizes the first
upload):

| Field | Value |
|---|---|
| PyPI project name | `tmuxkit` |
| Owner | `bkrabach` |
| Repository name | `tmux-kit` |
| Workflow filename | `publish.yml` |
| Environment name | `pypi` |

**GitHub — on the new repo:** create it (public), push the subtree-split
history, create the `pypi` environment in Settings → Environments
(protection rules optional; muxplex's precedent uses a plain
environment), enable branch protection on `main` to taste.

Exactness matters: muxplex's own publish.yml comment records that a
mismatched publisher triple fails the upload with a 403 *after* a green
build — the failure arrives at release time, not PR time.

## 7. What tmux-kit's repo carries beyond code

- `CONSUMERS.md` — already written to travel; update its status block
  (the "moving to its own repo" note becomes "this is that repo") and
  flip the dependency examples so PyPI is primary and the git form is
  the pinned/managed-environment variant.
- The §17 shared-server hazards (hook slot last-writer-wins, presence
  scoping, fence overlap) move from a plan-reference into the README
  proper — a public library cannot cite a private plan file as its
  safety documentation. The muxplex plan files stay authoritative for
  *why*; the README carries the *what*.
- `LICENSE` (MIT, matching the metadata already declared).

## 8. Proof gates — each a real install on a real machine, not a test

| Gate | Command / check | Proves | When |
|---|---|---|---|
| **G0** | Tree-hash identity: muxplex `v0.44.0:lib` subtree == tmux-kit `v0.44.0` content; then first upload lands | The published 0.44.0 is byte-what the fleet tested; the name window (§0.1) is closed | Before anything else ships |
| **G1** | Fresh container, **no git credentials**: `uv tool install muxplex && muxplex --version && muxplex doctor` | Public PyPI path resolves muxplex 0.44.0 + tmuxkit 0.44.0 from PyPI alone | Immediately after G0 — needs no muxplex release (ledger #10) |
| **G2** | Fleet-representative host: `uv tool install git+https://github.com/bkrabach/muxplex@v0.45.0` → doctor | Git path resolves with tmuxkit from the index; the workspace removal broke nothing | After S4 tags v0.45.0, before fleet roll |
| **G2b** *(only if §2.3 posture 2 pursued)* | Same install with the git source present; inspect `tmuxkit-*.dist-info/direct_url.json` in the tool venv | Whether the fleet's uv honors a project git source on git installs — the unproven claim (b) | Decides posture 2 vs posture 1; run on the fleet's pinned uv version |
| **G3** | Scratch project: `uv init && uv add tmuxkit && uv run python -c "import tmuxkit, sys; assert not any(m.startswith(('fastapi','httpx','pam')) for m in sys.modules); print(tmuxkit.__version__)"` | Standalone library install from PyPI. **Not** `uv tool install tmuxkit` — no entry points, refusal is correct behavior (ledger #7) | After G0 |
| **G4** | Fleet roll, host-by-host: record session count + `muxplex doctor` before; **verify `KillMode=process` / cgroup-escape before the service restart**; re-install per that host's path; restart; verify count identical + doctor green | Zero session loss across the transition | Last |

G4's bolded clause is the entire fleet-risk story: the packaging change
never touches tmux, but **the rollout restarts `muxplex.service`, and the
service restart is the historically lethal moment** (AGENTS.md, "Two ways
to destroy every live tmux session," mechanism 1 — 44 sessions,
2026-07-29). The pre-restart KillMode/canary check is not optional and
not new; it is the standing discipline, restated because a "harmless
packaging rollout" is exactly when it gets skipped.

Rollback story, per gate: G0–G3 failures block releases and touch no
host. A G4 failure on host N stops the roll; hosts 1..N−1 run v0.45.0
(byte-identical tmuxkit), hosts N.. stay on v0.44.0 git installs, which
remain valid indefinitely (ledger #9). There is no state migration in
this plan — nothing to roll back but a package version.

## 9. The bidirectional-flow cost, priced plainly

The extraction plan's §14.2 headline was: *"a lib fix rides the exact
rollout muxplex already has — there is no separate lib rollout to forget…
muxplex can never observe a lib version it was not tested against,
because they share a commit."* **This plan deletes that property.** That
is the price of the split, and it should be paid with eyes open:

| | Before (monorepo) | After (two repos) |
|---|---|---|
| A tmuxkit fix reaching muxplex | 1 PR, 1 tag, 1 rollout | 2 PRs (tmux-kit, then muxplex pin-bump + `uv lock`), 2 tags, 2 CI runs, **ordered** — the muxplex PR cannot even lock until the tmuxkit release is live on PyPI |
| Drift possibility | Impossible by construction | Real and permanent: muxplex pins 0.44.0 while tmux-kit walks to 0.47.0 |
| Atomic cross-cutting change (lib API + muxplex caller) | One commit | Impossible. Sequence: additive tmuxkit release → muxplex adopts → deprecation release. Breaking-in-place is gone |
| Dev loop for coupled changes | Edit two dirs | Temporary editable source + revert-before-commit (§5), guarded by CI |
| Test feedback for "did this lib change break muxplex" | Same-PR, same suite | Next pin-bump PR — unless the canary below runs |

Mitigations (which reduce, not erase):

- The `==` pin makes drift **visible** — a version delta in one file —
  never silent behavioral skew (§4).
- `test_tmuxkit_contract.py` (§3.3) makes a bad bump red in the PR that
  bumps.
- **The weekly canary** the extraction plan's §14.5 honesty clause
  already prescribed, now pointing across repos: a scheduled,
  non-blocking muxplex CI job that installs `tmuxkit @ git+…/tmux-kit@main`
  and runs the suite — drift becomes visible weekly instead of at the
  next deliberate bump. Renovate/Dependabot on the pin as the nudge.

**What makes this acceptable now, that was not a day ago:** (1) public
`uv tool install muxplex` is a hard requirement, PyPI forbids the
direct-URL escape, so tmuxkit must be on PyPI — and once it is published
*somewhere*, the marginal cost of the separate repo is the release dance
above, not the publishing itself; (2) a real second consumer needs a
front door that is not a personal server repo; (3) tmuxkit is the
slowest-moving code in the codebase (5 commits in 789 for the presence
rule — the extraction plan's §1 measurement), so the dance is paid
rarely. If tmuxkit's churn rises sharply, this trade should be re-read —
that would be evidence the seam was drawn too early, and the §0.2
monorepo-publishing shape remains the retreat position (move the repo
back, keep the PyPI name; PyPI does not care where uploads build from).

## 10. Sequence

Strictly ordered; each step gated before the next.

| Step | Action | Gate |
|---|---|---|
| **S0** | Owner pre-work: confirm the CISO policy reading (§2.4); create `bkrabach/tmux-kit` + `pypi` environment; add the PyPI pending publisher (§6.3 table) | Publisher visible on PyPI |
| **S1** | Claim the name / repair the wheel: subtree split, layout commit, port `test.yml`+`publish.yml`, tag `v0.44.0` **after** G0's tree-hash identity check; publish | **G0**, then **G1** and **G3** pass. The §0.1 window is closed. Public installs of muxplex 0.44.0 work — note: the public path is fixed *here*, three steps before the fleet is touched |
| **S2** | tmux-kit repo completeness: tests + harness + rails moved in and green in tmux-kit CI (ubuntu+macos, integration+differential) | tmux-kit CI green at `v0.44.0` |
| **S3** | If CISO posture 2 is wanted: run **G2b** on the fleet's uv version against a scratch branch | Decides §2.3 mechanism 1 vs 2; recorded in the runbook |
| **S4** | muxplex v0.45.0 PR (§5: workspace removal, lib deletion, lock, contract test, rails, CI guard); tag | muxplex CI green (requires S1); **G2** passes |
| **S5** | Fleet roll, host-by-host | **G4** — zero session delta, doctor green per host |
| **S6** | Docs closure: CONSUMERS.md flip (§7), extraction plan gets a two-line addendum pointing here, second app's pin guidance updated to `tmuxkit>=0.44.0` from PyPI (or the git form per its posture) | — |

## 11. One-way doors and do-not-build

| Decision | Why it is one-way | This plan's stance |
|---|---|---|
| The PyPI name `tmuxkit` | First upload binds it forever; filenames are immutable even after deletion (a deleted `tmuxkit-0.44.0.tar.gz` can never be re-uploaded — a botched 0.44.0 means 0.44.1 + a muxplex pin bump, never a re-release) | Claim it at S1; content gated by G0's tree-hash identity |
| First release = 0.44.0 | Whatever ships first is permanent | §4 — deliberately 0.44.0, to repair the live wheel |
| muxplex 0.44.0's published wheel | Cannot be changed; can only be yanked | Left alone — S1 makes it *work*. Yank only if the name-claim had been lost |
| **Do not** add `[tool.uv.sources]` git entry for tmuxkit "for safety" | It is unproven (ledger #4), version-dependent, and — post-S1 — unnecessary for both install paths | Only via S3/G2b evidence AND a confirmed CISO requirement |
| **Do not** loosen the pin to a range to "reduce the release dance" | Recreates silent drift — a fleet host running lib code no muxplex suite ever saw | §4; the dance is the honest cost, the canary is the mitigation |
| **Do not** copy any tmuxkit file into muxplex (or the second app) during the transition | The byte-similar-copy incident rule (extraction plan §14.3) applies doubly during a split | Move-only; the contract test + import smoke would catch a shadow copy |
| **Do not** publish tmuxkit from *both* repos "temporarily" | Two build provenances for one PyPI name is the confusion the trusted-publisher triple exists to prevent | muxplex's publish.yml never gains a tmuxkit build; the §0.2 alternative is a *retreat*, not a parallel path |
| Safety rails | Never weakened, only re-scoped: tmux-kit gets exactly-one-run-shell + purity + conftest rails; muxplex tightens to zero-run-shell + gains the cross-repo contract test | §3.2/§3.3 |

## 12. Owner pre-work checklist (nothing here is agent-doable)

1. Confirm the managed-device policy reading — §2.4's one sentence.
2. Create `bkrabach/tmux-kit` (public, empty).
3. GitHub: `pypi` environment on the new repo.
4. PyPI: pending publisher — `tmuxkit` / `bkrabach` / `tmux-kit` /
   `publish.yml` / `pypi` (§6.3, exact strings).
5. Decide whether the CISO runbook wants §2.3 mechanism 1 documented
   (one install line) — this is a doc decision, not a code one.
6. Schedule the fleet roll window; re-read AGENTS.md mechanism 1 before
   the first `systemctl restart` of the roll.

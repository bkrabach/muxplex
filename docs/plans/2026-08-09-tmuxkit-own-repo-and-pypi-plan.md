# tmux-kit to its own repo + PyPI — restore public installs, keep the git path

**Status:** design only, not implemented. Written against muxplex `main` at
v0.44.0 (lib/ shipped as a workspace member; muxplex 0.44.0 and
muxplex-client 0.44.0 on PyPI; muxplex's PyPI wheel currently
install-broken by its unresolvable `tmuxkit==0.44.0` pin).
**Revised 2026-08-08:** the owner renamed the library to **`tmux-kit` all
the way through** (was `tmuxkit`). The rename voids this plan's original
headline property — publishing 0.44.0 no longer repairs the broken muxplex
0.44.0 wheel — and re-opens the first-version decision. §0.0–§0.1 carry the
naming facts and the disposition of the orphaned old name; §3.0 sequences
the rename; §4 re-decides the version. Everything the rename does not touch
is unchanged from the first writing.
**Sequel to:** `2026-08-08-tmux-lib-extraction-plan.md` — this is the §14.5
"independent semver / own repo" step that plan named possible but did not
recommend. The owner has now decided it. This plan designs it honestly,
costs included.
**Scope:** packaging, distribution, and a mechanical rename. muxplex's
`/api/*` contract and all runtime behavior are unchanged. The library's
code is unchanged in behavior — it is renamed and it moves.

---

## 0. Four things to say before the design

### 0.0 The naming, made explicit — including the one hard language rule

- **PyPI distribution name and GitHub repo: `tmux-kit`.**
- **Python import package: `tmux_kit`.** This is not a compromise or a
  choice — hyphens are illegal in Python identifiers, so `import tmux-kit`
  cannot exist in the language. `pip install tmux-kit` → `import tmux_kit`
  is the standard arrangement (cf. `python-dateutil` → `dateutil`).
- **PEP 503 normalization, which cuts both ways:** `tmux-kit`, `tmux_kit`,
  and `tmux.kit` all normalize to the *same* PyPI name (`tmux-kit`) — one
  registration covers every spelling a consumer might type. But `tmuxkit`
  (no separator) normalizes to `tmuxkit` — a **different** PyPI name.
  Claiming `tmux-kit` does **nothing** for the old name. That fact drives
  §0.1.
- Directory: `lib/tmux_kit/` (until the split), `tmux_kit/` at the new
  repo's root (after). Every `import tmuxkit`, re-export shim, rail
  literal, conftest reference, harness import, and `CONSUMERS.md` example
  is renamed — §3.0 sequences exactly where that lands.

### 0.1 The rename VOIDS the two-birds property — and leaves the old name armed

The first writing of this plan had one elegant move: publish `tmuxkit`
0.44.0 and, in a single upload, (1) close the dependency-confusion window
on the name and (2) retroactively repair muxplex 0.44.0's already-published
wheel, whose `Requires-Dist: tmuxkit==0.44.0` would start resolving. **The
rename breaks both halves:**

1. muxplex 0.44.0's published wheel pins the OLD name. Publishing
   `tmux-kit` — any version — does not satisfy `tmuxkit==0.44.0` (PEP 503:
   different normalized name, §0.0). **muxplex 0.44.0 stays broken on PyPI
   permanently.**
2. `tmuxkit` is now **orphaned but still dangerous**: unregistered
   (verified 2026-08-08: `pypi.org/simple/tmuxkit/` → 404) while a
   published wheel pins it. Anyone can register `tmuxkit` 0.44.0 and have
   their code execute on a public `pip install muxplex==0.44.0`. The
   rename removed our *reason* to fill that name as part of normal work —
   which makes the window easier to forget, and forgetting it is the worst
   outcome available.

Also verified 2026-08-08: `pypi.org/simple/tmux-kit/` → 404. **Both names
are unclaimed. Both need a first upload** — one real, one defensive.

**Disposition of `tmuxkit` (one-way door, decided): tombstone + yank —
not real content, not nothing.**

- **Upload a tombstone** `tmuxkit` release (version `0.0.0` — deliberately
  *not* 0.44.0; see below) whose project description says "renamed to
  `tmux-kit`; this name is registered defensively" and whose sole module
  raises `ImportError` with the same message. Then **yank the tombstone
  release itself**: the project — and therefore the name — stays claimed
  (only project *deletion* frees a PyPI name; §11 forbids that forever),
  but nothing resolves by default, so nobody accidentally installs a stub.
  The project page still renders the pointer description.
- **Yank muxplex 0.44.0** (PEP 592). It has been install-broken since the
  moment of publication — no one has ever successfully installed it, so
  there are no working downstream users to protect. Yanking removes it
  from default resolution; per PEP 592 an *exact* pin (`muxplex==0.44.0`)
  still resolves a yanked release with a warning — and then fails, loudly
  and **un-hijackably**, at the `tmuxkit==0.44.0` resolution, because the
  tombstone owns that name and publishes no 0.44.0.
- **Why the tombstone must not be version 0.44.0:** a satisfiable 0.44.0
  stub would make `pip install muxplex==0.44.0` *succeed at install and
  break at import* — strictly worse than failing at resolution. If real
  content were ever published under the old name it would have to be the
  real library; see next paragraph for why it isn't.

**The cleaner-looking answer, considered and rejected** (stated per the
brief's request): publish the *real* 0.44.0 library under the old
`tmuxkit` name — built from `muxplex@v0.44.0:lib`, where the code still
carries the old import name — restoring the retroactive repair AND closing
the window in one upload. Rejected, three reasons: (i) it creates a
permanent second live distribution of the same code under a dead name, on
public PyPI where strangers pick packages by search — the byte-similar-copy
confusion surface the extraction plan's §14.3 rule exists to prevent, now
outward-facing; (ii) it "repairs" a release that has never once installed
successfully — there is no user it helps, and days later `pip install
muxplex` resolves 0.45.0 anyway; (iii) it commits the owner to visibly
maintaining-or-abandoning a second name forever. The tombstone captures
the entire security value at zero maintenance. Price of rejecting the
repair: muxplex 0.44.0 on PyPI is a permanent dead release — yanked,
loud-failing, harmless.

**Consequence for sequencing:** the tombstone + both yanks need no rename,
no split, no CI — they are a same-day action and go **first** (step S1,
§10). Do not let repo ceremony delay closing a live window. Note the fleet
is untouched by all of this — all seven hosts are git-installed; this
subsection is purely about the public PyPI surface.

### 0.2 The honest alternative the owner should see once before paying for the split

If the *only* goal were "make `uv tool install muxplex` work from PyPI
again," **no repo split is needed.** Rename in place, add one build line to
the existing `publish.yml` (`uv build --package tmux-kit`) plus one trusted
publisher on PyPI, and the library publishes from the monorepo — keeping the extraction
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
  direct dependency" — warehouse's own error); a plain `tmux-kit>=0.1.0`
  spec sails through. This half is safe to build on.
- **(b) whether `uv tool install git+…/muxplex` honors muxplex's own
  `[tool.uv.sources]` git entry — UNPROVEN and uv-version-dependent.** The
  docs frame sources as development-time; uv demonstrably ignores sources
  of *transitive* dependencies (astral-sh/uv #11388). The shipped state
  proves only the `{ workspace = true }` variant works on the fleet's uv
  versions — a *different code path* (workspace discovery inside one
  checkout) from a cross-repo `{ git = … }` source. **Do not bet the
  design on (b).**
- **The reframing that dissolves the bet:** once tmux-kit is *on PyPI*, the
  git-install path does not need a source entry at all. `uv tool install
  git+…/muxplex` has *always* resolved fastapi, uvicorn, and httpx from an
  index — the git path was never a no-index path. After S3, tmux-kit is
  just one more index-resolved dependency, reaching CISO devices through
  whatever index/mirror already serves fastapi. The dual-declaration
  mechanism is therefore **not the design; it is an optional hardening**
  for one specific org posture (§2.3), with a uv-version-independent
  fallback (`--with 'tmux-kit @ git+…'`) if (b) is false when tested.

So the recommended muxplex `pyproject.toml` after the split is the boring
one: `tmux-kit==<version>` in `[project.dependencies]`, **no
`[tool.uv.sources]` entry for tmux-kit at all.** One declaration, both
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
| 5b | `tmux-kit` also unregistered on PyPI | **Verified 2026-08-08 (post-rename)** | `pypi.org/simple/tmux-kit/` → 404. Both names need first uploads — one real, one tombstone (§0.1) |
| 5c | PEP 503: `tmux-kit` ≡ `tmux_kit` ≡ `tmux.kit`, but ≢ `tmuxkit` | True by the normalization rule | Runs of `-`/`_`/`.` collapse to `-`; `tmuxkit` has no separator to collapse, so it is a distinct PyPI name. Claiming the new name does nothing for the old (§0.0) |
| 5d | Yank semantics (PEP 592): yanked releases are skipped by default resolution but still resolve under an *exact* `==` pin, with a warning | Per PEP 592 / pip behavior | Why yanking muxplex 0.44.0 shrinks but does not erase the exact-pin path — and why the tombstone must own `tmuxkit` so that residual path fails un-hijackably (§0.1) |
| 6 | tmux-kit is stdlib-only — zero transitive index fetches on any path | **Verified** | `lib/pyproject.toml`: `dependencies = []`, with the stay-empty contract comment; enforced by `test_lib_import_smoke.py` and the AST import-purity rail |
| 7 | `uv tool install tmux-kit` as a proof gate | **WRONG — corrected** | the library has no `[project.scripts]`; `uv tool install` refuses packages with no executables. The standalone gate is `uv add tmux-kit` + `import tmux_kit` smoke (§8, G3) |
| 8 | Trusted Publishing precedent exists in-house | Verified | muxplex's `publish.yml` already uses OIDC (`id-token: write`, `environment: pypi`, `pypa/gh-action-pypi-publish`), with a comment documenting the 403-on-missing-publisher failure mode |
| 9 | Old fleet tags keep working during the transition | True by construction | git tags are immutable; `uv tool install git+…/muxplex@v0.44.0` still sees `lib/` (old name and all) in that tag's tree after the rename and deletion land on `main` |
| 10 | ~~Publishing 0.44.0 retroactively repairs the published muxplex 0.44.0 wheel~~ | **VOID — killed by the rename** | The wheel pins the OLD name `tmuxkit`; no `tmux-kit` release can satisfy it (ledger 5c). The public-install fix now lands only with muxplex **0.45.0** (which pins `tmux-kit`) — see §8's revised G1 timing. muxplex 0.44.0 on PyPI is yanked as a permanent dead release (§0.1) |

## 2. The mechanics, resolved

### 2.1 What the published muxplex wheel declares

`[project.dependencies]` carries `tmux-kit==<pinned version>` — a plain
version specifier. That is what enters `Requires-Dist`, what PyPI accepts,
and what public `uv tool install muxplex` resolves from PyPI. Nothing else
is available to a wheel: claim (a) is settled.

### 2.2 What the git install resolves

`uv tool install git+https://github.com/bkrabach/muxplex@vX` builds muxplex
from the checkout and resolves its dependencies — fastapi, uvicorn, …, and
now tmux-kit — from the configured index, exactly as it already does for
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
     --with 'tmux-kit @ git+https://github.com/bkrabach/tmux-kit@v0.1.0'
   ```
   A direct-URL requirement at the *operation* level pins tmux-kit's source
   regardless of how uv treats project sources. Verify post-install via
   the tool venv's `tmux_kit-*.dist-info/direct_url.json` (dist-info
   directories use the underscore form). This is one
   documented line in the CISO install runbook — no repo mechanism at all.
2. **Project-level git source (`[tool.uv.sources] tmux-kit = { git = …,
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
tmux-kit-on-PyPI (or on the same mirror, once vetted like fastapi was)
needs nothing special. Under the stricter first-party-from-git reading,
§2.3's ladder applies. **Pre-work item for the owner: confirm which
reading is the actual policy** — one sentence from the device policy
decides whether mechanism 1 goes in the runbook or nothing is needed.

And the transitive question: tmux-kit's `dependencies = []` (verified,
enforced by test) — a git-sourced tmux-kit adds **zero** index fetches.

## 3. Repo creation and history

### 3.0 The rename lands in the muxplex repo FIRST — then the split carries renamed code

The rename touches both sides of the seam at once: the library's own
package directory and metadata, AND muxplex's imports, re-export shims,
both safety rails (which name the path `lib/tmux_kit/` and the package
prefix `tmux_kit.` as literals), `conftest.py`, and the differential
harness. **The only place all of that can change atomically and be proven
green together is the monorepo — and this is the last atomic
cross-cutting change the monorepo will ever host. Use it deliberately.**
A rename executed "as part of standing up the new repo" would land half
in each repo with no single commit anywhere that the full suite ever
validated — that is the named failure mode, and the split below is
forbidden until the rename is green in muxplex.

Exact order, two commits (separated so git's rename detection and future
`blame` stay clean):

- **R1 — pure move, nothing else:** `git mv lib/tmuxkit lib/tmux_kit`.
  No content edits in this commit.
- **R2 — mechanical rename, everything else:**
  - `lib/pyproject.toml`: `name = "tmux-kit"`,
    `[tool.hatch.build.targets.wheel] packages = ["tmux_kit"]`.
  - Every `import tmuxkit` / `from tmuxkit …` in `muxplex/` (re-export
    shims included), `muxplex/tests/` (conftest, differential harness,
    all lib tests), and `lib/tmux_kit/` itself → `tmux_kit`.
  - Both rails' literals: the run-shell scan's allowed-site path and the
    import-purity rail's package prefix.
  - muxplex `pyproject.toml`: dependency `tmuxkit==0.44.0` →
    `tmux-kit==0.44.0`; `[tool.uv.sources]` key `tmuxkit` → `tmux-kit`
    (still `{ workspace = true }` at this stage — the split hasn't
    happened yet). `uv lock`.
  - The differential recordings are data (recorded manifest/epoch/name
    tuples), not imports — but **verify** none embeds a module path
    before asserting that; any that do are rewritten in R2, never left
    for the new repo to discover.
- **Gate:** full muxplex suite + `-m differential` + both rails green at
  R2. Only then may the split run.

The interim `main` (R2 → v0.45.0) still workspace-resolves the lib, so
git installs from `main` keep working; fleet hosts sit on immutable
v0.44.0 tags and see nothing (ledger #9).

### 3.1 `bkrabach/tmux-kit`, public, via `git subtree split` — of the already-renamed tree

**Decision: subtree split of `lib/` (post-R2), not a clean start, not a
deep filter-repo rewrite.** The new repo is born with the correct name:
it never contains a `tmuxkit/` directory and never hosts a rename commit.

```
# in the muxplex repo, at or after R2
git subtree split --prefix=lib -b tmux-kit-export
# in the new, empty bkrabach/tmux-kit
git pull <muxplex-repo> tmux-kit-export
```

Followed in the new repo by one **identity commit** with exactly three
enumerated deltas (and nothing else — G0 diffs against this list):
flatten `lib/` scaffolding so `tmux_kit/` sits at the root; set
`version = "0.1.0"` (§4); point `[project.urls]` at the new repo.

- *Why not clean start:* the lib-era commits (extraction stages, the
  round-trip contract fix, rail changes, the R1/R2 rename) are the design
  record of the code
  as it exists; they are small, recent, and free to keep. The subtree
  split follows the `lib/` prefix, and R1's pure-move commit keeps rename
  detection (and therefore `git blame` through the rename) working.
- *Why not filter-repo with path renames to carry pre-extraction history:*
  the `git mv` from `muxplex/*.py` into `lib/` means the deep
  history lives under paths outside the prefix; carrying it requires a
  full rewrite that interleaves unrelated server history — cost without
  benefit, because **the incident rationale lives in docstrings and
  tests, which travel as file content**, and the muxplex repo remains the
  permanent archaeological record (it is not going anywhere). The new
  repo's README gets a one-line pre-history pointer:
  *"History before 2026-08-08 lives in bkrabach/muxplex (extracted per
  docs/plans/2026-08-08-tmux-lib-extraction-plan.md)."*

Layout in the new repo after the identity commit: `pyproject.toml` +
`tmux_kit/` at the root (drop
the `lib/` nesting — it was workspace scaffolding), `tests/`, `README.md`,
`CONSUMERS.md` (already written to travel), `.github/workflows/`.

### 3.2 What must travel with the code — non-negotiable

Per the extraction plan's §8.4 rule (*a seam that strands an incident test
is wrong*), the split moves, not copies:

- **All tmux-kit unit + incident tests** currently in `muxplex/tests/`:
  presence-rule tests, the multi-window bell finding, the `.`→`_` mangling
  refusal, casefold+fnmatchcase platform tests, cgroup-escape tests, the
  stdlib-only import smoke.
- **The differential harness and its recordings**
  (`test_differential_harness.py`, `-m differential`). The recordings are
  fleet-captured data; they are the regression bed for the presence rule
  and they belong with the function they test.
- **The library half of the never-render rail:** *exactly one `run-shell`
  construction site in `tmux_kit/**`, in `bell.py`, with no parameter that
  can request a loud variant* — an AST scan in tmux-kit's own suite
  (path literal already renamed by R2, §3.0).
- **The safety-rail conftest fixtures the integration tests depend on:**
  isolated `TMUX_TMPDIR` (autouse), `TMUX` unset, never a bare
  `kill-server` — tmux-kit's new `tests/conftest.py` reimplements these
  (they are small), because its integration tests run a real tmux and the
  rails are what has kept real tmux servers alive through this suite.
- **The import-purity rail** becomes tmux-kit's simplest test: no module
  under `tmux_kit/` imports anything outside stdlib + `tmux_kit.*`.

### 3.3 What stays in muxplex — and the one new cross-repo test

- Tests of muxplex's *use* of tmux-kit (poll-cycle integration, restore
  integration, bell-hook arming, everything driving `muxplex.main`).
- muxplex's never-render rail **tightens to zero**: no `run-shell`
  construction site anywhere in `muxplex/**` (the extraction's §3.2
  two-rail scheme, now split across two repos).
- **New: `test_tmux_kit_contract.py`** — the cross-repo drift tripwire,
  modeled on `test_client_contract.py`. Runs against the *installed*
  tmux-kit at the pinned version and asserts: mirrored constants equal
  (`DEFAULT_CAPTURE_LINES`, `MAX_CAPTURE_LINES`, `ALLOWED_KEYS`,
  `MAX_KEYS`); `build_alert_bell_hook`'s signature has no loudness
  parameter (introspection — the no-loud-variant property, checkable from
  outside); `import tmux_kit` drags in no fastapi/httpx/pam
  (`sys.modules` smoke); presence round-trips an unknown top-level key.
  This is what turns a bad pin bump red in the muxplex PR that made it.
- `test_safety_rails.py::test_library_tests_live_under_the_railed_tests_dir`
  is **retired** in the same PR that deletes `lib/` — its premise ends.
  Retire by replacement (point it at the new contract test's existence),
  never by silent deletion; the rail file's own module docstring demands
  that discipline.

## 4. Versioning — a one-way door, RE-DECIDED after the rename

The first writing chose `0.44.0`, and that choice was justified *entirely*
by one mechanism: matching the published muxplex wheel's `tmuxkit==0.44.0`
pin so the upload retroactively repaired it. **The rename voids that
rationale completely** (ledger #10 — the wheel pins the old name; nothing
published as `tmux-kit` can ever satisfy it). The version is a free choice
again, and it must be re-argued, not inherited.

**Decision: tmux-kit's first PyPI release is `0.1.0`.**

- **The number should tell the truth to its actual audience.** `tmux-kit`
  is a brand-new public name whose interface is explicitly *not frozen*
  (the extraction plan's stage-6 gate has not been passed; 0.x, no semver
  promise). A first release numbered 0.44.0 signals 43 prior public
  releases and an API maturity the public surface has not earned; a
  stranger evaluating it would reasonably look for a changelog that does
  not exist. `0.1.0` signals exactly what is true: young public API,
  expect movement.
- **The continuity argument died with the rename.** Keeping 0.44.0 would
  make the number continuous with a *different distribution name's*
  private, never-on-PyPI history — that's more confusing to a public
  audience than clarifying. What continuity actually needs to survive is
  code identity, and that lives where it always did: gate **G0**'s
  mechanical tree comparison ("tmux-kit 0.1.0 is byte-identical, modulo
  the §3.0 rename and the §3.1 identity commit, to the library the fleet
  ran inside muxplex v0.44.0"), one provenance line in the README, and
  muxplex's CHANGELOG.
- **Cost, acknowledged:** the 1:1 number mapping to muxplex tags is lost;
  future archaeology ("which lib matched muxplex v0.44.0?") needs the
  README line instead of arithmetic. Small, and paid once.
- **What would have flipped it back:** if any *published artifact* pinned
  the new name at an inherited version, matching it would win — that is
  precisely the situation the old name was in and the rename dissolved.
  No such artifact exists for `tmux-kit`; muxplex 0.45.0 will be the
  first, and it pins whatever this section says.

From 0.1.0 the lines are decoupled from birth: tmux-kit advances on its
own 0.x cadence (no semver promise until the extraction plan's stage-6
freeze — that gate is unchanged by split or rename); muxplex bumps its
pin deliberately. muxplex 0.45.0 (the release that drops `lib/`) pins
`tmux-kit==0.1.0` — byte-identical code modulo rename, now resolved from
PyPI, which keeps the packaging change and any behavior change impossible
to conflate.

**Pin policy: exact `==`, not a range** — unchanged by the rename.
muxplex is an application; applications pin. Under 0.x, a range is a
fiction (0.x minors may break), and an open range would let a fleet host
resolve a tmux-kit that no muxplex test ever ran against — recreating
silently the drift the monorepo made impossible. The `==` pin makes drift
*visible as a version delta* and makes "tested together" a property of
every muxplex release. Its cost is the two-step dance, priced in §9.

## 5. muxplex repo changes (lands as v0.45.0)

The rename itself (R1/R2, §3.0) has already landed on `main` before this
PR. Ordered within one PR, after tmux-kit 0.1.0 is live on PyPI:

1. `pyproject.toml`: remove `"lib"` from `[tool.uv.workspace] members`;
   delete `tmux-kit = { workspace = true }` from `[tool.uv.sources]`; set
   the dependency to `tmux-kit==0.1.0` with the comment rewritten to name
   the new repo and the pin-bump discipline. **No git source entry**
   (§2.3 — only if G2b proves it and the CISO posture demands it).
2. Delete `lib/` (the split already carried it out). Tombstone in
   `CHANGELOG.md`, not a stub directory.
3. `uv lock` — tmux-kit now resolves from PyPI into the lockfile with a
   registry source and hash.
4. `publish.yml`: the explicit `--package` build flags stay as-is (the
   comment that anticipated exactly this future stays accurate; its
   why-not-built-here paragraph now points at the new repo). **One
   addition — the broken-wheel preflight:** after `uv build`, before
   upload, resolve the freshly built wheel against the real index in a
   scratch env (`uv venv && uv pip install --dry-run dist/muxplex-*.whl`).
   This is the guard that makes the incident this whole plan exists to
   fix — *publishing a wheel whose pinned dependency is not on PyPI* —
   structurally unrepeatable, including for every future pin bump.
5. Tests per §3.3: move the lib tests out, add `test_tmux_kit_contract.py`,
   tighten the never-render rail to zero, retire the
   lib-tests-location rail.
6. **New CI guard:** assert the committed `pyproject.toml` has no `path`
   or `git` source for tmux-kit. Reason: the cross-repo dev loop (below)
   works by *temporarily* adding one, and the predictable failure is
   committing it — which would silently turn every git install into a
   moving-target resolve and break `uv build` reproducibility.

**The cross-repo dev loop, documented (it gets worse; say so):** working
on both at once is no longer "edit two directories in one repo." The
workflow: `uv add --editable ../tmux-kit` (writes a temporary path
source), develop, then *revert the pyproject change* before committing —
the CI guard in (6) catches the miss. Single-repo tmux-kit work needs
nothing special.

**CI ordering hazard:** muxplex's CI cannot go green on this PR until
tmux-kit 0.1.0 is actually resolvable from PyPI. Strict order: S1–S4
before S5 (§10). Note `test-latest-deps`-style jobs that install with
bare pip/uv against PyPI now *also* exercise the tmux-kit resolution —
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
| PyPI project name | `tmux-kit` |
| Owner | `bkrabach` |
| Repository name | `tmux-kit` |
| Workflow filename | `publish.yml` |
| Environment name | `pypi` |

(A pending publisher does **not** reserve the name against someone else's
classic upload — only a real first upload claims a PyPI name. The
publisher config is authorization, not reservation; the claim happens at
S3's first publish, and the tombstone claim at S1 happens by upload too.)

**The `tmuxkit` tombstone upload (§0.1) — mechanics:** a one-time act for
a dead name, so a permanent trusted-publisher config is ceremony it does
not deserve. Recommended: an **account-scoped API token created, used
once for the tombstone upload, and revoked the same day** (a
project-scoped token cannot exist before the project does). Alternative,
if the owner prefers zero tokens ever: a second pending publisher
(project `tmuxkit`, repo `muxplex`, a one-off `publish-legacy-tombstone.yml`,
environment `pypi`), deleted after the single run. Either way: upload the
0.0.0 tombstone, verify the project page renders the renamed-to pointer,
then yank the release — and never delete the project (§11).

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
  flip the dependency examples so PyPI is primary
  (`dependencies = ["tmux-kit>=0.1.0"]`, `import tmux_kit`) and the git
  form (`tmux-kit @ git+https://github.com/bkrabach/tmux-kit@v0.1.0`) is
  the pinned/managed-environment variant. State the §0.0 naming rule
  (`pip install tmux-kit` → `import tmux_kit`) where consumers will see
  it first.
- The §17 shared-server hazards (hook slot last-writer-wins, presence
  scoping, fence overlap) move from a plan-reference into the README
  proper — a public library cannot cite a private plan file as its
  safety documentation. The muxplex plan files stay authoritative for
  *why*; the README carries the *what*.
- `LICENSE` (MIT, matching the metadata already declared).

## 8. Proof gates — each a real install on a real machine, not a test

| Gate | Command / check | Proves | When |
|---|---|---|---|
| **GT** | Clean env: `pip install tmuxkit` resolves **nothing** (tombstone yanked); the project page renders the renamed-to pointer; `pip install muxplex==0.44.0` fails at the `tmuxkit==0.44.0` resolution — and *cannot* be made to succeed by any third party | The §0.1 window is closed; the residual exact-pin path is fail-loud and un-hijackable | Immediately after S1 (tombstone + yanks) — before any rename/split work |
| **GR** | muxplex `main` at R2: full suite + `-m differential` + both rails green; `uv tool install git+…/muxplex@<R2 sha>` → `muxplex doctor` | The rename is complete and atomic in the one repo that holds both sides (§3.0); interim git installs still resolve via the workspace | Before the split may run |
| **G0** | Tree identity: the split head (muxplex `lib/` at R2+) == tmux-kit `v0.1.0` content, modulo the three enumerated identity-commit deltas (§3.1); then first upload lands | The published 0.1.0 is byte-what the fleet tested (modulo rename); the `tmux-kit` name (ledger 5b) is claimed by us | Before S3's publish |
| **G1** | Fresh container, **no git credentials**: `uv tool install muxplex && muxplex --version && muxplex doctor` | Public PyPI path resolves muxplex 0.45.0 + tmux-kit 0.1.0 from PyPI alone. **Timing changed by the rename:** this can only pass after muxplex 0.45.0 is published (ledger #10 void) — the public-install fix now lands at S5, not S1 | After S5 publishes v0.45.0 |
| **G2** | Fleet-representative host: `uv tool install git+https://github.com/bkrabach/muxplex@v0.45.0` → doctor | Git path resolves with tmux-kit from the index; the workspace removal broke nothing | After S5 tags v0.45.0, before fleet roll |
| **G2b** *(only if §2.3 posture 2 pursued)* | Same install with the git source present; inspect `tmux_kit-*.dist-info/direct_url.json` in the tool venv | Whether the fleet's uv honors a project git source on git installs — the unproven claim (b) | Decides posture 2 vs posture 1; run on the fleet's pinned uv version |
| **G3** | Scratch project: `uv init && uv add tmux-kit && uv run python -c "import tmux_kit, sys; assert not any(m.startswith(('fastapi','httpx','pam')) for m in sys.modules); print(tmux_kit.__version__)"` | Standalone library install from PyPI, and the §0.0 name arrangement (`tmux-kit` dist → `tmux_kit` import) works as documented. **Not** `uv tool install tmux-kit` — no entry points, refusal is correct behavior (ledger #7) | After S3 |
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
(byte-identical library code, modulo rename), hosts N.. stay on v0.44.0
git installs (old name and all, ledger #9), which
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
| A tmux-kit fix reaching muxplex | 1 PR, 1 tag, 1 rollout | 2 PRs (tmux-kit, then muxplex pin-bump + `uv lock`), 2 tags, 2 CI runs, **ordered** — the muxplex PR cannot even lock until the tmux-kit release is live on PyPI |
| Drift possibility | Impossible by construction | Real and permanent: muxplex pins 0.1.0 while tmux-kit walks to 0.4.0 |
| Atomic cross-cutting change (lib API + muxplex caller) | One commit | Impossible. Sequence: additive tmux-kit release → muxplex adopts → deprecation release. Breaking-in-place is gone (the §3.0 rename was the last one) |
| Dev loop for coupled changes | Edit two dirs | Temporary editable source + revert-before-commit (§5), guarded by CI |
| Test feedback for "did this lib change break muxplex" | Same-PR, same suite | Next pin-bump PR — unless the canary below runs |

Mitigations (which reduce, not erase):

- The `==` pin makes drift **visible** — a version delta in one file —
  never silent behavioral skew (§4).
- `test_tmux_kit_contract.py` (§3.3) makes a bad bump red in the PR that
  bumps.
- **The weekly canary** the extraction plan's §14.5 honesty clause
  already prescribed, now pointing across repos: a scheduled,
  non-blocking muxplex CI job that installs `tmux-kit @ git+…/tmux-kit@main`
  and runs the suite — drift becomes visible weekly instead of at the
  next deliberate bump. Renovate/Dependabot on the pin as the nudge.

**What makes this acceptable now, that was not a day ago:** (1) public
`uv tool install muxplex` is a hard requirement, PyPI forbids the
direct-URL escape, so tmux-kit must be on PyPI — and once it is published
*somewhere*, the marginal cost of the separate repo is the release dance
above, not the publishing itself; (2) a real second consumer needs a
front door that is not a personal server repo; (3) tmux-kit is the
slowest-moving code in the codebase (5 commits in 789 for the presence
rule — the extraction plan's §1 measurement), so the dance is paid
rarely. If tmux-kit's churn rises sharply, this trade should be re-read —
that would be evidence the seam was drawn too early, and the §0.2
monorepo-publishing shape remains the retreat position (move the repo
back, keep the PyPI name; PyPI does not care where uploads build from).

## 10. Sequence

Strictly ordered; each step gated before the next.

| Step | Action | Gate |
|---|---|---|
| **S0** | Owner pre-work: confirm the CISO policy reading (§2.4); create `bkrabach/tmux-kit` + `pypi` environment; add the PyPI pending publisher for `tmux-kit` (§6.3 table); mint-and-plan-to-revoke the tombstone token (§6.3) | Publisher visible on PyPI |
| **S1** | **Name hygiene, same-day, no code:** upload the `tmuxkit` 0.0.0 tombstone, yank the tombstone release, yank muxplex 0.44.0 (§0.1). Independent of all rename/split work — do not let it wait on any of it | **GT** — the confusion window is closed before any other work begins |
| **S2** | **Rename in the monorepo** (§3.0): R1 pure `git mv`, R2 mechanical rename (imports, shims, rails, conftest, harness, pyproject, lock) | **GR** — full suite + differential + both rails green; interim git install works |
| **S3** | Split + publish: `git subtree split --prefix=lib`, identity commit (flatten, `0.1.0`, URLs), port `test.yml`+`publish.yml`, tmux-kit CI green (ubuntu+macos, integration+differential), tag `v0.1.0` **after** G0's tree identity check; publish | **G0**, then **G3**. The `tmux-kit` name is claimed with real content |
| **S4** | If CISO posture 2 is wanted: run **G2b** on the fleet's uv version against a scratch branch | Decides §2.3 mechanism 1 vs 2; recorded in the runbook |
| **S5** | muxplex v0.45.0 PR (§5: workspace removal, lib deletion, pin `tmux-kit==0.1.0`, lock, contract test, rails, CI guard, publish preflight); tag + publish | muxplex CI green (requires S3); **G2** passes; **G1** passes — *this* is the step that restores public `uv tool install muxplex`, a consequence of the rename (ledger #10) |
| **S6** | Fleet roll, host-by-host | **G4** — zero session delta, doctor green per host |
| **S7** | Docs closure: CONSUMERS.md flip (§7), extraction plan gets a two-line addendum pointing here, second app's pin guidance updated to `tmux-kit>=0.1.0` from PyPI (or the git form per its posture) | — |

The honest timing delta vs the first writing, stated once: the public
`pip/uv install muxplex` repair used to land at S1 with zero muxplex
release; the rename moves it to **S5**, because only a muxplex release
that pins the *new* name can be publicly installable. Between S1 and S5
the public path stays broken — but now *safely* broken (yanked +
un-hijackable), which is the property that actually matters in the gap.

## 11. One-way doors and do-not-build

| Decision | Why it is one-way | This plan's stance |
|---|---|---|
| The PyPI name `tmux-kit` (≡ `tmux_kit`, ledger 5c) | First upload binds it forever; filenames are immutable even after deletion (a deleted `tmux_kit-0.1.0.tar.gz` can never be re-uploaded — a botched 0.1.0 means 0.1.1 + a muxplex pin bump, never a re-release) | Claim it at S3 with real content; gated by G0's tree identity |
| The PyPI name `tmuxkit` (old, distinct — ledger 5c) | While unclaimed it is a live dependency-confusion target against the published muxplex 0.44.0 wheel; **project deletion frees a PyPI name**, so the claim must be held forever | Tombstone at S1 (0.0.0, then yank the release); **never delete the project**; never publish real content or a 0.44.0 under it (§0.1) |
| First `tmux-kit` release = 0.1.0 | Whatever ships first is permanent | §4 — re-decided after the rename voided the 0.44.0 rationale |
| muxplex 0.44.0's published wheel | Cannot be changed; can only be yanked | **Yanked at S1** — it pins a name we will never publish real content under; it is a permanent dead release, made fail-loud and un-hijackable rather than repaired (§0.1, ledger #10) |
| The import name `tmux_kit` | Baked into every consumer's source the moment anyone imports it | Fixed by the language rule (§0.0); renamed once, at R1/R2, before any external consumer exists |
| **Do not** run the split before GR is green | A rename half-landed across two repos has no commit anywhere that the full suite validated | §3.0 — the split consumes only already-renamed history |
| **Do not** add `[tool.uv.sources]` git entry for tmux-kit "for safety" | It is unproven (ledger #4), version-dependent, and unnecessary for both install paths once tmux-kit is on PyPI | Only via S4/G2b evidence AND a confirmed CISO requirement |
| **Do not** loosen the pin to a range to "reduce the release dance" | Recreates silent drift — a fleet host running lib code no muxplex suite ever saw | §4; the dance is the honest cost, the canary is the mitigation |
| **Do not** copy any tmux-kit file into muxplex (or the second app) during the transition | The byte-similar-copy incident rule (extraction plan §14.3) applies doubly during a split | Move-only; the contract test + import smoke would catch a shadow copy |
| **Do not** publish tmux-kit from *both* repos "temporarily" | Two build provenances for one PyPI name is the confusion the trusted-publisher triple exists to prevent | muxplex's publish.yml never gains a tmux-kit build (the one-off tombstone for the *old* name is a different project and a single yanked upload); the §0.2 alternative is a *retreat*, not a parallel path |
| Safety rails | Never weakened, only re-scoped: tmux-kit gets exactly-one-run-shell + purity + conftest rails (literals renamed at R2, never loosened); muxplex tightens to zero-run-shell + gains the cross-repo contract test | §3.0/§3.2/§3.3 |

## 12. Owner pre-work checklist (nothing here is agent-doable)

1. Confirm the managed-device policy reading — §2.4's one sentence.
2. Create `bkrabach/tmux-kit` (public, empty).
3. GitHub: `pypi` environment on the new repo.
4. PyPI: pending publisher — `tmux-kit` / `bkrabach` / `tmux-kit` /
   `publish.yml` / `pypi` (§6.3, exact strings).
5. PyPI: mint an account-scoped token for the one-time `tmuxkit`
   tombstone upload; revoke it the same day (§6.3). Then yank the
   tombstone release and yank muxplex 0.44.0 (§0.1) — the yank buttons
   are owner-only web-UI actions.
6. Decide whether the CISO runbook wants §2.3 mechanism 1 documented
   (one install line) — this is a doc decision, not a code one.
7. Schedule the fleet roll window; re-read AGENTS.md mechanism 1 before
   the first `systemctl restart` of the roll.

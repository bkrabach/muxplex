# Implementation Specification: Auto-Updating Views (glob rules)

Status: **MERGED to `main` on 2026-08-04 (`8e8692f`..`b78a944`, four commits) — NOT
YET IN A RELEASE.** The newest tag, `v0.35.0`, predates all four, so an installed
muxplex has manual-only views. Retained as an architectural decision record.

Source brief: `docs/BACKLOG.md` item 1, deleted in `b78a944` per that backlog's own
graduation rule. Do not re-add it.

**Scope discipline (as written, and as shipped):** this adds ONE optional key inside an
existing settings structure and ONE predicate to an existing filter. It does not build a
rule engine, a query language, or a subsystem.

**Read §0.1 first — it is the most valuable section in the document.** The originating
brief's implied delivery mechanism (put rule evaluation behind `GET /api/view` and stop)
would have shipped a feature that renders **empty in the PWA**. Three of the four surfaces
that consume view membership re-derive it *client-side* from `settings.views[].sessions` —
the PWA grid/dropdown/sidebar/Manage View (`app.js` `filterVisible()`), muxplex-deck's
`resolve_view()`, and the soft deck's picker counts; only the soft deck's session list
asks the server. `views.filter_visible()` had exactly one server-side caller in the repo.
The fix that shipped is additive and is the load-bearing decision here: every session dict
in `GET /api/sessions` and `GET /api/federation/sessions` carries a resolved
`views: [<name>, ...]`, so client membership logic *shrinks* instead of being ported again.

Five decisions below are settled and will be re-litigated by anyone who skips them:

- **Glob matches the bare session name, one syntax, no device qualifier.** The qualifier is
  a UUID, not a hostname, so `spark-1:*` can never match anything; a pattern containing
  `:` is rejected at validation rather than silently never matching
  (`views.view_patterns`, `views.validate_view_rules`). §0.2.
- **Membership is the UNION of rules and manual pins, resolved fresh on every read.** The
  server must **NEVER** materialize a rule match back into `view["sessions"]` — that
  re-introduces the exact decay this feature exists to eliminate, turns every poll cycle
  into a settings write, and hands federation LWW a new race. This prohibition is also
  recorded in `AGENTS.md` because it is load-bearing beyond this document.
- **Exclusions are deliberately out of v1**, with the shape pre-committed so adding them
  later is not a redesign.
- **Ordering falls through to the existing sort.** A view must not become a fourth ordering
  authority.
- **Attention is a SORT, permanently — never a view.** Looking at a session clears
  `needs_attention`, so an attention *view* would empty itself as you used it, and it would
  immediately demand set composition to be useful. §0.6.

**Open follow-up, real and unshipped:** muxplex-deck (separate repo, independent release)
renders a rule-based view as **empty** until it reads the new `views` field. Manual views
are unaffected. The remedy is the ~5-line change specified in §10.2; the `muxplex_client`
half already shipped here (§10.1), which is what keeps it to five lines instead of a port
of the matcher. Not a blocker for this repo — but it is a live gap, not a hypothetical.

---

## 0. Read first — three findings that change the plan, and one thing not to build

The brief's constraints all check out against the code. Its **implied delivery mechanism does
not**, and the fix is the single most important decision in this document. Read §0.1 before
anything else.

### 0.1 `GET /api/view` is NOT where view membership reaches most clients — three of four surfaces re-derive it

The brief says, correctly, that `docs/API_SEMANTICS.md` states the direction plainly: resolve
server-side. It then implies the consequence is "put rule evaluation behind `GET /api/view` and
you are done." **That would ship a feature that is empty in the PWA.**

Verified reality — every consumer of view membership, and where it actually computes it:

| Surface | Where membership is computed | Source data |
|---|---|---|
| **PWA grid, dropdown counts, sidebar, Manage View** | `frontend/app.js:1073` `filterVisible()` — a **client-side port** | `_serverSettings.views[].sessions` |
| **muxplex-deck (hardware sidecar)** | `muxplex-deck/src/muxplex_deck/views.py` `resolve_view()` — a **client-side port**, called at `main.py:563` | `GET /api/settings` `views[].sessions` |
| **Soft deck picker counts** | `frontend/deck/deck.js:1250` `viewSessionCounts()` — a **client-side port** | `GET /api/settings` `views[].sessions` |
| Soft deck session list | `GET /api/view` (server-resolved) ✅ | server |

`views.filter_visible()` has **exactly one server-side caller** in the whole repo:
`main.py:1261`, inside `GET /api/view`. Confirmed:

```
$ grep -rn "filter_visible\|visible_count" muxplex/*.py | grep -v views.py
main.py:143   (import)
main.py:1261  visible = filter_visible(raw_sessions, settings, active_view)
```

So "rule evaluation belongs on the server" is the right *direction* and is **not yet the
delivered reality**. If rules only teach `filter_visible` about globs, a rule-based view
renders correctly on exactly one surface — the soft deck's session list — and renders **empty**
on the PWA grid, the PWA's view dropdown counts, the Manage View panel, the hardware deck, and
the soft deck's own picker counts.

**Consequence — the load-bearing design decision (§4):** rule resolution must reach clients on
the payload they already poll for membership purposes: the **session list**. Every session dict
returned by `GET /api/sessions` and `GET /api/federation/sessions` gains an additive
`views: [<view name>, ...]` field — the server's resolved answer for "which user views does this
session belong to." Client membership logic then *shrinks* (an array lookup replaces a
dual-key search), which is the opposite of porting more logic. `filter_visible` gains the same
rule awareness so `GET /api/view` is correct with **zero changes to its handler**.

This is the mechanism that satisfies the brief's actual requirement — "the PWA, soft deck, and
sidecar cannot disagree" — rather than the endpoint the brief guessed at.

### 0.2 The device qualifier is a **UUID**, not a hostname — which settles open question 1 on evidence, not taste

The brief (and the backlog) suggest `spark-1:*` is "a natural thing to want to type." It is
natural to want, and it **cannot work**:

- `views[].sessions` entries are `"<device_id>:<name>"` where `device_id` is
  `identity.load_device_id()` → **`str(uuid.uuid4())`** (`muxplex/identity.py:46`), e.g.
  `d502b663-1f0a-4c8e-…`. The backlog's own example shows this.
- `spark-1` is a **`device_name`** — a *different* settings field (`settings.py:47`), and for
  remote peers it is `remote_instances[].name`, which is **per-observer local config**: two
  devices can legitimately call the same peer different things.
- So `spark-1:*` matches nothing on any device, forever, and a *correct* qualified glob would
  require typing a UUID prefix.

**Two syntaxes are therefore not a trade-off, they are a trap.** §2.1 decides one syntax
against the bare session name, and rejects `:` in a pattern at validation time with an error
that names this reason.

### 0.3 `GET /api/sessions` returns **no `sessionKey` at all** — the qualified key isn't even available to the clients that need it

`main.py:1037` `get_sessions()` returns `{name, snapshot, bell, last_activity_at}`. No
`sessionKey`. Only the federation aggregate (`main.py:3094`, `:3177`) adds one.

This is why `app.js:1079`'s `keyOf(s)` is `s.sessionKey || s.name`, and why muxplex-deck's
`views.py` matches on the `":<name>"` **suffix** — neither has the qualified key in
single-device mode. A rule defined against the qualified key would be unevaluable on exactly
the payload most clients poll, and would behave differently in single-device vs federated mode.
The bare `name` is present on every session dict, from every endpoint, in every mode. §2.1.

### 0.4 The destructive-write backstop makes "convert a manual view to a rule view" a 409 — which settles open questions 2 and 5

`views.assess_views_destruction()` (`views.py:86`) rejects a write where total session-member
count across all views drops by ≥50% (`DESTRUCTIVE_MEMBER_DROP_RATIO`). Converting a 20-pin
manual view into a rule-only view in one PATCH drops members 20 → 0 and returns **409
`{"backstop": true}`** — `patchSettingsGuarded()` surfaces that as a hard failure, not a retry.

The design that avoids this entirely is **union** (§2.2): rules and pins coexist; adding a rule
never removes a pin. That makes §2.5 (what happens to an existing manual view) a non-event —
no migration, no refusal, nothing to write twice.

### 0.5 Nested keys inside a view dict survive round-trip — including through an older peer

Verified: `settings.load_settings()` (`settings.py:281`) and `save_settings()`
(`settings.py:584`) iterate **top-level** `DEFAULT_SETTINGS` keys and copy the `views` **value
wholesale**. `enforce_mutual_exclusion`, `normalize_session_keys`, and `prune_stale_keys` all
touch only `view["sessions"]`. Nothing anywhere strips an unrecognized key from inside a view
dict.

**Therefore:** a `match_names` key added inside a view entry round-trips through disk, through
`PATCH`, through federation sync, and **through a muxplex instance that predates this feature**
— that older peer stores it, ignores it, shows the view's pins only, and hands `match_names`
back intact on the next sync. That is real version tolerance in both directions, and it is what
makes §3's storage decision safe. (Contrast: a new **top-level** settings key would be silently
erased by any older peer's next write — the `session_commands`/`DEFAULT_SETTINGS` data-loss trap
documented in `docs/plans/2026-08-02-named-session-command-pairs-plan.md` §0.2.)

### 0.6 Do NOT build: attention-as-a-view, and set composition

Two rule types are **closed here, not deferred**. Reasons in §2.6 and §2.7. Summary:

- **Attention is a SORT, permanently.** It already exists as one, in three implementations
  AGENTS.md requires to move together. As a view it would have *self-emptying membership*
  (looking at a session clears `needs_attention`), and it would immediately demand set
  composition to be useful ("attention within my work view") — the line the backlog says not to
  cross. The `attention` sort already solves this by *reordering* rather than *removing*.
- **Set composition is rejected.** Any rule that references another view turns the view list
  into a dependency graph (cycles, evaluation order, partial failure) and makes `filter_visible`
  recursive. Nothing about the self-healing prize requires it.

### 0.7 Known, bounded degradation you are accepting by shipping this

**muxplex-deck (separate repo, independent release) will show a rule-based view as empty** until
it is updated to read the new `views` field (§10). Manual views are entirely unaffected. The
empty render is *honest* — it matches that sidecar's documented "unknown view → empty, never
silently substitute" behavior — but a user with the hardware deck will see it. The remedy is a
~5-line change in that repo, specified in §10.2; the `muxplex_client` half of it ships **in this
repo** (§10.1) so the sidecar change is a one-liner.

---

## 1. What ships

A view may carry an optional list of **fnmatch-style glob patterns matched against the bare tmux
session name**. A session is in the view if it is pinned **or** matches a pattern. Resolution
happens server-side, in one function, and reaches every client on payloads they already poll.

```json
{
  "name": "Amplifier",
  "sessions": ["d502b663-1f0a-4c8e-9c31-2f1b0a77b6de:team-pulse-manager"],
  "match_names": ["amplifier-*", "*-agent"]
}
```

Everything else about views is unchanged: `hidden` stays a reserved pseudo-view and stays
orthogonal, `views_updated_at` keeps its meaning, the destructive-write backstop keeps guarding
writes (and now guards rules too, §3.4), pruning keeps pruning pins (and cannot touch rules,
§7.3), and a view with no `match_names` behaves byte-identically to today.

---

## 2. The open questions — decided

### 2.1 Q1: bare name, device-qualified key, or either? → **Bare session name. One syntax.**

`match_names` patterns are matched against `session["name"]` — never against `sessionKey`, never
against `"<device_id>:<name>"`.

**Why, in order of decisiveness:**

1. **The qualifier is a UUID (§0.2).** `spark-1:*` cannot ever match; the thing a user would
   type is a `device_name`, which is not what is in the key, and for remote peers is
   *per-observer* config — a name-keyed pattern synced fleet-wide would resolve to different
   sessions on different hosts. A syntax whose obvious use is always wrong is worse than no
   syntax.
2. **The qualified key is not available where matching must happen (§0.3).** `GET /api/sessions`
   carries only `name`. Matching on `name` is uniform across single-device and federated mode,
   local and remote sessions, every endpoint.
3. **`amplifier-*` already means "on any device," which is the case the backlog says is the real
   one.** Device scoping is a *separate axis* and gets a separate field if it is ever built
   (§2.8) — not an escape sequence inside a name pattern.
4. **A pattern containing `:` is structurally impossible**, because tmux forbids `:` in session
   names (stated in `docs/API_SEMANTICS.md`, and the reason clients may match by `":<name>"`
   suffix at all). That gives us a free, unambiguous validation rule: **reject any pattern
   containing `:`** with an error naming the reason and pointing at the deferred device rule
   (§6.2 V4). The door for `devices: [...]` stays open without ever having shipped an ambiguous
   syntax.

**Matching semantics** — identical technique to the already-shipped, already-documented
`input_allowed_sessions` fence: explicit `.casefold()` on both sides, then
`fnmatch.fnmatchcase`. Deliberately **not** plain `fnmatch.fnmatch`, whose case folding comes
from `os.path.normcase` and is therefore platform-dependent (no-op on Linux, folding on
macOS/Windows). See `terminal_input.session_matches_allowlist`'s docstring
(`terminal_input.py:90-129`) for the full rationale — it applies verbatim.

**Deliberately NOT reusing `terminal_input.session_matches_allowlist` itself.** §5.1 specifies a
separate `views.matches_name_pattern()` with the identical three-line body and a cross-reference
comment in both places. Rationale: that function is *the entire security boundary* for the RCE-
by-design `/input` endpoint. A shared helper would mean a future tightening of the fence (say,
rejecting `**`, or narrowing the charset) silently changes which sessions a *view* contains, and
a future loosening for views silently widens an RCE fence. Two consumers with opposite failure
requirements — fail-closed security vs. fail-loud display — must not share a mutable
implementation. The duplication is nine lines and is the cheap side of that trade.

### 2.2 Q2: can a view mix rules and manual pins? → **Yes. Strict union. Pins are additive-only.**

`members(view) = set(view["sessions"]) ∪ {s : any(match(s.name, p) for p in valid(view["match_names"]))}`

**Why:**

- It is what makes Q5 a non-event (§2.5) — adding a rule to a manual view changes nothing about
  the pins.
- It sidesteps the backstop 409 (§0.4). The user never has to make the write that trips it.
- The "obviously fiddly" part is real and **bounded to exactly one case**: you cannot *un-pin* a
  session the rule matches, because it is not pinned. §9.3 specifies that the UI must **say so**
  — a rule-matched member's remove control is disabled with a reason, not offered as a no-op.
  This is the one place the union's cost surfaces, and surfacing it honestly is cheaper than any
  precedence rule that tried to hide it.
- Provenance ("is this session here because of a pin or a rule?") needs **no new server field**:
  every client already has the view's `sessions` array from `GET /api/settings`, so
  `matched_by_rule = annotated && !pinned`. §9.3.

### 2.3 Q3: exclusions in v1? → **Out. Deferred, with a same-day substitute and a pre-committed shape.**

Not shipped: no `exclude`, no `!pattern` negation, no precedence rules.

**Why this is a decision and not a punt:**

1. **A working exclusion mechanism already ships and costs nothing:** `hidden_sessions`.
   `filter_visible` removes hidden sessions from every user view (`views.py:250`). "`amplifier-*`
   minus that one noisy scratch session" is expressible **today**, the day this ships, by hiding
   that session. It is not equivalent — hiding is global, not per-view — and that difference is
   exactly the signal worth collecting before building a second mechanism. If real use shows
   people hiding sessions they only want gone *from one view*, that is the evidence exclusions
   need.
2. **Exclusion is the first step onto the query-language path the backlog names as the line not
   to cross.** Include + exclude is one grammar step from boolean composition, and every rule
   type is one the server, PWA, soft deck, and sidecar agree on forever.
3. **The shape is pre-committed so a later addition costs no redesign** (write this down now,
   build nothing): a sibling `exclude_names: [str]` on the same view, same matcher, same
   validation. Applied **after** `match_names` and **never** to `sessions` pins — an explicit
   pin always wins over an exclusion, which is the only precedence rule that avoids an
   unresolvable argument about intent.

### 2.4 Q4: ordering within an auto view? → **Falls through to the existing sort. A rule never carries ordering.**

**Why:**

1. **Ordering is already a settled, three-authority axis and a view must not become a fourth.**
   `settings.sort_order` (synced), `GET /api/view?sort=` (per-request, per-client), the soft
   deck's own local `sort`, and muxplex-deck's own `config.sort`. `deck.js:1780-1793` quotes
   `DECK_PARITY_ARCHITECTURE.md` §2.1/§6.1 on precisely this split: *which sessions* is one
   server-owned answer; *which of the server's orderings a client requests* is a client
   preference. A per-view sort would silently override the user's own global preference on
   every surface, and would have to be reconciled against three existing authorities.
2. **There is no existing per-view ordering to preserve.** `filter_visible` iterates the **live
   session list** and tests membership (`views.py:249`) — it does not iterate `view["sessions"]`.
   So the order of the pins array is *already* not honored today. Rules take nothing away.
3. **It is not what the feature is for.** The prize named in the backlog is membership that
   cannot decay. Ordering is orthogonal and already solved.

### 2.5 Q5: existing manual view + someone adds a rule? → **Coexist. No migration, no refusal, no prompt.**

Adding `match_names` to an existing view is a pure addition. Pins stay. Nothing is rewritten,
no schema version bumps, no one-shot upgrade pass runs, and `views_updated_at` advances exactly
as it does for any other `views` edit (`settings.py:717`).

**And the corresponding prohibition, which is load-bearing:**

> **The server MUST NEVER materialize rule matches back into `view["sessions"]`.** Rules stay
> rules on disk, forever.

Materializing would (a) re-introduce exactly the decay this feature exists to eliminate, (b) turn
every ~2s poll cycle into a settings write, (c) bump `views_updated_at` continuously and hand
federation LWW a brand-new race, and (d) mean a device with fewer live sessions writes a smaller
`views` that trips the destructive-write backstop against its own peers. This prohibition is
also the entire reason the federation claim in §7 holds.

### 2.6 The one that had to be decided anyway: **attention is a SORT, not a view. Permanently.**

Do not build an attention rule type. Do not build an attention pseudo-view. This is closed, not
deferred.

1. **It already exists as a sort**, server-resolved at `main.py:1134` `_attention_order()`, and
   mirrored in `frontend/app.js` `sortByAttention()` and muxplex-deck's `attention.py` — three
   implementations that AGENTS.md explicitly requires to move together. An attention *view* adds
   a fourth home for attention semantics and the first one that could disagree about
   **membership** rather than merely order.
2. **Attention state is volatile and self-clearing.** The predicate is
   `unseen_count > 0 and (seen_at is None or last_fired_at > seen_at)` — *looking at a session
   clears it*. A view whose membership empties as you look at it is a categorically different
   object from every other view: tiles would vanish under the cursor, and the session you are
   currently attached to would drop out of the view you are currently in. The `attention` **sort**
   handles this correctly and deliberately: a cleared session *moves to tier 3*, staying on
   screen. A view's only vocabulary is removal. This is not a polish difference; it is the wrong
   primitive.
3. **A sort composes; a view does not.** With the sort you can already ask for
   "attention-first *within* my work view" — one request, `GET /api/view?sort=attention`. With
   an attention view you would have to intersect it with the work view, i.e. **set composition**,
   which §2.7 rejects. Attention-as-a-view *requires* the feature we have ruled out. That, on its
   own, is decisive.
4. **The precedent argues the same way.** The tier-3 sort-key incident (keying off
   `last_activity_at` reordered the grid on every 2s poll because tmux `#{window_activity}` bumps
   on any pane output) shows how much care attention *ordering* already needed. Membership churn
   on the same signal would be strictly worse — and unlike ordering churn, it removes tiles.

### 2.7 Set composition (union / intersection / difference of views): **rejected**

The backlog calls it "probably the line not to cross." This spec agrees and states the criterion
so a future proposal can be measured against it: **any rule type that references another view
turns `settings.views` from a list into a dependency graph** — cycles to detect, evaluation
order to define, partial-failure semantics to invent, and `filter_visible` to make recursive
(with an evaluation budget, on a hot path, on every poll). None of that serves the self-healing
prize. If a real need appears, the honest answer is a saved *search*, which is a different
feature with a different name.

### 2.8 The other candidate rule types — deferred, each with its reason and its blocker

| Rule | Verdict | Reason |
|---|---|---|
| **Device / host** | Deferred (most likely second) | Needs a decision this spec deliberately refuses to force: key on `device_id` (stable, a UUID nobody will type) or `device_name` (human, mutable, non-unique, and for remotes it is `remote_instances[].name` — **per-observer local config**, so one synced rule would mean *different sessions on different hosts*). Pre-committed shape: a sibling `devices: [<device_id>]`, **AND**ed with `match_names`, plus whatever UI turns a picked device into an id. |
| **Activity window** ("active in last N hours") | Deferred | Cheap to compute, but membership becomes a function of wall-clock time with no event to drive a re-render, making every count on every surface answer-at-the-instant-you-asked. Same volatility objection as attention (§2.6) **without** attention's existing sort answer. If it returns, it is probably a sort. |
| **Working directory** | Deferred | Not blocked, just not free: requires exposing cwd (new session field + a per-poll tmux query). Revisit on demand. |
| **Regex** | Deferred | Globs must be shown to fall short first. Also a distinct hazard globs don't have: catastrophic backtracking evaluated for every session on every poll, on input that arrives via `PATCH`/federation sync rather than a local-file-only fence — i.e. genuinely untrusted, unlike `input_allowed_sessions`. |
| **Set composition** | **Rejected** (§2.7) | Turns the feature into a query language. |
| **Attention state** | **Rejected** (§2.6) | It is a sort, and building both would be the mistake. |

---

## 3. Storage

### 3.1 Shape — extend the existing entry, do not add a key

`settings.views` stays a list of objects. One new **optional** key per entry:

```jsonc
{
  "name": "Amplifier",                  // unchanged, still validated by validate_view_name
  "sessions": ["<device_id>:<name>"],   // unchanged: manual pins
  "match_names": ["amplifier-*"]        // NEW, optional, list[str] of glob patterns
}
```

**Why extend rather than add a parallel `auto_views` settings key:**

- A second list doubles every reader in the system — the PWA dropdown, sidebar, Manage View,
  Views settings tab, `filter_visible`, `assess_views_destruction`, `prune_stale_keys`,
  `normalize_session_keys`, `GET /api/view`'s cycle list, `muxplex_client.Settings.views`,
  muxplex-deck's `ViewCycler` — and forces a name-collision rule between the two lists that every
  one of those readers must agree on.
- The nested key round-trips safely today, including through pre-feature peers (§0.5). A new
  **top-level** key would be silently erased by any older peer's next write.
- `views_updated_at` already advances on any `views` edit (`settings.py:717`) — a rule edit is
  arbitrated by exactly the timestamp designed for it, with zero new plumbing.

**Field name.** `match_names`, not `match`/`rules`/`patterns`. It answers the #1 open question in
the name itself — patterns match **names** — for every reader of `settings.json` and every future
client author, and it leaves room for a differently-keyed sibling (`devices`, `exclude_names`)
without ambiguity. It also matches house style (`input_allowed_sessions`,
`stale_key_grace_hours`, `new_session_template`).

### 3.2 `DEFAULT_SETTINGS` — no change

`views` is already a top-level key with default `[]`. Nothing to add. (Contrast
`session_commands`, which needed a `DEFAULT_SETTINGS` entry precisely because it was top level.)

### 3.3 `SYNCABLE_KEYS` / `LOCAL_ONLY_KEYS` — no change

`views` is already in `SYNCABLE_KEYS` (`settings.py:256`). Rules sync with it. Rules must **not**
join `LOCAL_ONLY_KEYS`: unlike `session_commands` / `new_session_template`, a glob pattern names
no command and no filesystem path, the server never executes it, and it grants no capability —
worst case it displays the wrong set of session names to someone who already has read access to
all of them. The `LOCAL_ONLY_KEYS` fence exists for keys "that name a command or a filesystem
path the server itself later executes or reads" (`settings.py:178-204`); this is not one, and
widening a security fence to cover a display filter would dilute the rule that makes the fence
legible.

### 3.4 The destructive-write backstop must count rules as members — 2-line change

`views._view_member_count()` (`views.py:76`) currently sums `len(v["sessions"])`. A rule-bearing
view with zero pins contributes 0, which means the incident-driven protection at
`DESTRUCTIVE_MEMBER_DROP_RATIO` **weakens exactly as views migrate to rules** — a stale client
that PATCHes back a `views` array with every `match_names` stripped would sail through.

Change `_view_member_count`'s per-view body to add a second term, in the same defensive style as
the existing one (`v` is already `isinstance`-checked as a dict by the enclosing loop):

```python
sessions = v.get("sessions")
if isinstance(sessions, list):
    total += len(sessions)
patterns = v.get(VIEW_RULE_KEY)          # "match_names"
if isinstance(patterns, list):
    total += len(patterns)
```

Count **raw** entries, including structurally invalid ones (a non-string in the list still counts
as a member) — the backstop is measuring *how much configuration is about to disappear*, not how
much of it is valid.

Behavior for every existing config is **byte-identical** (no `match_names` → +0). Thresholds
(`DESTRUCTIVE_VIEW_DROP_RATIO`, `DESTRUCTIVE_VIEW_COLLAPSE_THRESHOLD`,
`DESTRUCTIVE_MEMBER_DROP_RATIO`) do not change.

---

## 4. Where rules are evaluated — one matcher, two consumers

**Server-side, in `views.py`, in exactly one function.** Two call paths consume it:

```
views.matches_name_pattern(name, pattern)        <- the ONLY glob implementation
        │
        ├── views.view_names_for_session(...)  ──► views.annotate_view_membership(...)
        │                                             │
        │                                             ├── GET /api/sessions          (main.py:1037)
        │                                             └── GET /api/federation/sessions (main.py:3058)
        │                                                    │
        │                                                    └──► PWA grid/counts/sidebar/Manage View
        │                                                         soft-deck picker counts
        │                                                         muxplex-deck (after §10.2)
        │
        └── views.filter_visible(...)          ──► GET /api/view (main.py:1174) — handler UNCHANGED
                                                        │
                                                        └──► soft-deck session list, agents,
                                                             muxplex_client consumers
```

**What this implies for `GET /api/view` — stated explicitly, as the brief asks:**

1. **Its handler does not change.** Correctness arrives through `filter_visible`, which it
   already calls at `main.py:1261`. Its response shape does not change. Its `views:` cycle list
   does not change (it is built from `settings.views` names, which rules do not affect). Its
   `?sort=` behavior does not change.
2. **Do NOT add the `views` annotation to `/api/view`'s `sessions[]` entries.** There is no
   consumer (that payload *is* the resolved view already), and the endpoint's docstring commits
   to staying light for frequent polling (a Stream Deck dial). Additive surface stays minimal.
3. **Do NOT put rule validation errors on `/api/view`.** They belong on the config-level endpoint
   in §5.4, for the reasons in §6.4.
4. **`/api/view` remains local-sessions-only, unchanged.** It is not, and does not become, the
   membership source for the PWA — see §0.1. Do not extend it to federation as part of this work.

**Do NOT port the matcher to any client.** Not to `app.js`, not to `deck.js`, not to
muxplex-deck. Clients read the annotation. This is the whole point.

---

## 5. Server implementation

### 5.1 `muxplex/views.py` — new public functions

Add near the top of the file, after `MAX_VIEW_NAME_LENGTH`:

```python
VIEW_RULE_KEY: str = "match_names"
```

#### `matches_name_pattern(name: str, pattern: str) -> bool`

Pure. Returns `fnmatch.fnmatchcase(name.casefold(), pattern.casefold())`.

Docstring must state: (a) matching is deliberately case-insensitive via explicit `casefold()` +
`fnmatchcase` rather than `fnmatch.fnmatch`, because the latter's folding is a side effect of
`os.path.normcase` and is therefore platform-dependent; (b) this is the same technique as
`terminal_input.session_matches_allowlist` and is **deliberately a separate implementation** —
that one is the security boundary for the `/input` RCE fence, this one is a display filter, and
they must be able to evolve independently (§2.1).

Non-`str` `pattern` or `name` → return `False` (never raise; a malformed settings.json must not
500 a poll cycle). Validity is reported separately by `validate_view_rules`, never by silence
alone.

#### `view_patterns(view: object) -> list[str]`

Returns the **structurally valid** patterns of one view entry, in file order. `[]` unless `view`
is a dict whose `match_names` is a `list`. From that list keep an entry iff it is a `str`, is
non-empty, and contains no `":"`. Patterns are used **verbatim** — no trimming, no normalization
(a leading space is a legitimate, if unusual, part of a session name). Everything dropped here is
reported by `validate_view_rules`; nothing is dropped silently.

Invalid patterns are **excluded from matching** (they match nothing) — never silently widened to
match everything, and never fatal. Mirrors `resolve_session_commands`'s "invalid entry is
EXCLUDED, never silently degrades into the default."

#### `view_names_for_session(session: dict, settings: dict) -> list[str]`

The resolved list of **user-view names** this session belongs to, in `settings["views"]` order.
Pure; no I/O.

- Returns `[]` for an entry with a truthy `status` (federation status tiles are not sessions).
- For each view: member if `_key_of(session) in view["sessions"]` **or**
  `session["name"] in view["sessions"]` (the existing dual-lookup, unchanged) **or**
  `any(matches_name_pattern(session["name"], p) for p in view_patterns(view))`.
- **Does not consider `hidden_sessions`.** Hidden is orthogonal (schema v2) and stays a separate,
  rule-free membership test that clients already perform. Never returns `"all"` or `"hidden"`.

#### `annotate_view_membership(sessions: list[dict], settings: dict) -> list[dict]`

Returns a **new list of new dicts**, each `{**s, "views": view_names_for_session(s, settings)}`.
Status entries get `"views": []` (annotate everything, so no client has to null-check).

> **Must not mutate its input, and this is not stylistic.** `GET /api/federation/sessions` stores
> its tagged remote session dicts *in `_federation_cache`* (`main.py:3182`) and re-serves those
> same objects on later cycles (`main.py:3202`). In-place annotation would bake a point-in-time
> membership answer into the cache and serve it after the settings that produced it had changed.
> Build new dicts.

#### `filter_visible(...)` — one line changes

In the user-view branch (`views.py:245-250`), extend `in_view`:

```python
patterns = view_patterns(user_view)

def in_view(s: dict) -> bool:
    return (
        _key_of(s) in members
        or s.get("name", "") in members
        or any(matches_name_pattern(s.get("name", ""), p) for p in patterns)
    )
```

`"all"` and `"hidden"` branches are untouched. Hidden filtering is untouched — a rule-matched
session that is hidden is still excluded unless `include_hidden=True`, exactly like a pinned one.

#### `validate_view_rules(views: object) -> list[str]`

Pure. Returns human-readable error strings (empty list = clean). Rules in §6.2. **Only
`match_names` is inspected** — see §6.3 for why nothing else in `views` gains validation here.

### 5.2 `muxplex/settings.py` — validation on the PATCH path only

Add alongside `DestructiveSettingsWriteRejected` (`settings.py:593`):

```python
class InvalidViewRuleRejected(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))
```

In `patch_settings()` (`settings.py:614`), **before** the destructive-write backstop block
(cheapest and most specific check first, and a malformed payload should not be reported as a
near-miss on a backstop threshold):

```python
if "views" in patch:
    errors = validate_view_rules(patch["views"])   # lazy import, same pattern as line 644
    if errors:
        for e in errors: _log.error("settings: %s", e)
        raise InvalidViewRuleRejected(errors)
```

**Rejects the entire patch, writing nothing** — not even unrelated keys in the same request.
Consistent with the backstop's own all-or-nothing rule, and the only behavior that keeps a
client's model and the server's from diverging after a partial apply.

`apply_synced_settings()` (`settings.py:724`) — **no validation, no rejection.** A peer's
malformed rule is stored as sent, surfaced at read time (§6.4), and matches nothing. Three
reasons: one bad peer must never break fleet-wide settings sync; the peer may be running a
*newer* muxplex whose rules we do not yet understand; and rejecting would delete an operator's
data on a device that is not the one they edited.

### 5.3 `muxplex/main.py` — annotate the two session-list endpoints

**`GET /api/sessions` (`main.py:1037`).** Load settings once per request and annotate:

```python
settings = load_settings()
...
return annotate_view_membership(result, settings)
```

This adds one small-JSON file read to a ~1/s-per-client endpoint. Accepted deliberately:
`GET /api/view`, `GET /api/federation/sessions`, and `GET /api/state` already do exactly this on
the same cadence, and the alternative — a cached settings snapshot — is hidden state with an
invalidation bug waiting in it. **No caching, no memoization.** Matching cost is
`sessions × views × patterns` `fnmatchcase` calls against `fnmatch`'s own compiled-pattern LRU:
at 50 sessions × 10 views × 3 patterns that is ~1500 cached-regex matches, tens of microseconds,
against a handler that already shells out to tmux via the poll cache.

**`GET /api/federation/sessions` (`main.py:3058`).** Annotate the **final merged list**, as the
last step before returning, on **both** return paths (the early `return local_sessions` at
`:3099` and the merged return at the end). Annotating the merged list — not the per-remote
`tagged` lists — is what keeps `_federation_cache` un-annotated (§5.1). It already loads settings
at `:3067`; reuse that dict.

Local views apply to remote sessions, and that is correct: `views` is a synced setting, so view
definitions are fleet-global by design; each device annotates the sessions it is showing with the
view definitions it holds.

**`GET /api/view` (`main.py:1174`) — no change.** §4.

### 5.4 `muxplex/main.py` — `GET /api/views` (new, additive)

The plural sibling of `GET /api/view`, and a one-for-one structural copy of
`GET /api/session-commands` (`main.py:1108`) — the repo's established, proven pattern for
"canonical server-side resolution + the validation errors that go with it."

```
GET /api/views
->
{
  "views": [
    {
      "name": "Amplifier",
      "sessions": ["<device_id>:<name>", ...],
      "match_names": ["amplifier-*"],     // valid patterns only, in file order
      "errors": ["views[0] 'Amplifier': match_names[1] ..."]   // this view's errors
    }
  ],
  "errors": ["views[0] 'Amplifier': match_names[1] ..."]        // flat, all views, same strings
}
```

- Auth: the shared middleware. **Not** in `auth._AUTH_EXEMPT_PATHS`.
- Reports **user-defined views only** — no `"all"`, no `"hidden"`. `GET /api/view` already
  publishes the cycle list including the pseudo-views; this endpoint is about definitions.
- `match_names` contains only the patterns that will actually be used (invalid ones are absent
  and named in `errors`), so a client never has to decide validity for itself — the same rule
  `GET /api/session-commands` establishes for the command-pair fold.
- **Carries no session data**, therefore never goes stale as sessions come and go, therefore
  costs nothing to fetch on a settings-change trigger rather than a poll (§6.4, §9.2).

### 5.5 `muxplex/main.py` — `PATCH /api/settings` error mapping

In `update_settings()` (`main.py:1870`), catch the new exception alongside the existing
`DestructiveSettingsWriteRejected` handler:

```
400 {
  "detail": "<first error, or a joined summary>",
  "invalid_view_rule": true,
  "errors": [ ... ]
}
```

`invalid_view_rule: true` is the fourth member of the established "tell this 4xx apart from the
others" convention, alongside `backstop: true`, `terminal_conflict: true`, and
`unknown_command_id: true` (`docs/API_SEMANTICS.md`). **400, not 409:** the body is malformed,
not conflicted — retrying with fresh settings cannot help, so a client must not treat it like a
CAS miss.

---

## 6. Validation and failure behavior

### 6.1 Principle

A malformed glob **fails loud**, at the earliest boundary that can see it, and stays visible
afterward. It never silently matches everything, never silently matches nothing without
reporting, and never crashes a poll cycle.

Three entry paths, three treatments:

| Path | Treatment | Where the user sees it |
|---|---|---|
| `PATCH /api/settings` (PWA, agents, CLI `muxplex config set`) | **Reject the whole request, 400 `invalid_view_rule`, no write** | Immediately, in the UI that made the edit |
| Direct edit of `~/.config/muxplex/settings.json` | Accepted at write (nothing intercepts a file edit); pattern excluded from matching | `logger.error` at read time + `GET /api/views` `errors[]` + Settings badge (§9.2) |
| Federation sync from a peer | Accepted and stored verbatim; pattern excluded from matching | Same as above, on every device that received it |

The precedent is explicit: malformed `session_commands` entries were invisible until a recent
release surfaced them in Settings and the New Session picker. This spec adopts that release's
*exact* mechanism rather than inventing a second one — a resolution endpoint with `errors[]`, a
count badge on both gear buttons, and an error list inside the relevant Settings tab (§9.2).

### 6.2 Rules (`validate_view_rules`), each producing one error string and excluding one pattern

For each entry of `views` that is a dict, when the key `match_names` is **present**:

| # | Rule | Error string (format) |
|---|---|---|
| R1 | `match_names` must be a `list` | `views[{i}] '{name}': match_names must be a list of strings (got {type})` |
| R2 | each entry must be a `str` | `views[{i}] '{name}': match_names[{j}] must be a string (got {type})` |
| R3 | each entry must be non-empty | `views[{i}] '{name}': match_names[{j}] may not be empty` |
| R4 | each entry must not contain `':'` | `views[{i}] '{name}': match_names[{j}] may not contain ':' -- tmux session names cannot contain ':', so this pattern can never match. Patterns match the bare session name only; device-scoped rules are not supported.` |

R1 excludes the whole rule (the view keeps its pins); R2–R4 exclude one pattern.

**Explicitly NOT rules** (and why, so a builder does not add them):

- **No length cap.** It would prevent nothing real and adds an arbitrary number to a synced
  schema that every client would eventually have to know.
- **No "must contain a glob metacharacter."** A literal `amplifier` is a valid single-session
  pattern, exactly as in `input_allowed_sessions`.
- **No rejection of `*`.** A view that is "everything" is a legitimate thing to want.
- **No rejection of whitespace or shell metacharacters.** Sessions created outside muxplex can
  legitimately carry them (`is_valid_session_name` constrains only names *muxplex creates*), and
  the pattern never reaches a shell — `matches_name_pattern` is a pure string comparison.
- **No duplicate detection.** Duplicate patterns are harmless under a union and reporting them
  would be noise.

### 6.3 Nothing else in `views` gains validation

`validate_view_rules` inspects `match_names` and nothing else. A non-dict entry in `views`, a
missing `name`, a non-list `sessions` — all are tolerated exactly as today (silently, by the
existing defensive `isinstance`/`.get()` reads throughout `views.py`).

**Why:** `PATCH /api/settings` accepts those payloads today. Rejecting them now would be a
behavior change to *previously-valid* requests — precisely the kind of non-additive break
AGENTS.md forbids, in a contract with clients this repo's tests cannot see. New validation
applies only to the new key. (`validate_view_name` at `views.py:412` remains what it is today: a
helper the frontend uses, with no server-side enforcement. Do not wire it in as part of this
work.)

### 6.4 Why the read-time error channel is a separate endpoint

A write-time 400 cannot cover the file-edit and federation-sync paths — there is no request to
attach an error to. So a read-time channel is mandatory, and it must land somewhere the PWA
already looks.

- **Not `GET /api/settings`**: that endpoint returns stored settings with redaction; computing a
  resolution result there is exactly the pattern `GET /api/session-commands` was created to avoid
  (`docs/API_SEMANTICS.md`: "Clients MUST NOT re-derive this fold from `GET /api/settings`").
- **Not `GET /api/view`**: the PWA does not call it (§0.1), and it is committed to staying light
  for high-frequency polling.
- **Not the session-list payload**: config-level errors have no business riding a ~1/s poll.

Hence `GET /api/views` (§5.4): cheap, session-free, fetched on the trigger the PWA already has
for view-definition changes (`followRemoteViewDefinitions()`, `app.js`).

### 6.5 Degradation is bounded and reported, never hidden

An invalid pattern leaves the view showing **its pins plus its valid patterns** — visibly
narrower, never wider, never empty-by-surprise if pins exist. That is a degradation, not a hidden
fallback, because the same condition simultaneously produces: a `logger.error` line, an entry in
`GET /api/views`'s `errors[]`, a count badge on both Settings gear buttons, and a listed error
inside Settings → Views. If any of those four is dropped during implementation, the "fail loud"
requirement is not met.

---

## 7. Federation

### 7.1 The claim, verified

The backlog's reasoning — *"rules are far smaller and change far less often than materialized
lists, so this design probably reduces that race surface"* — is **correct, and for a stronger
reason than size.** The stored `views` array for a rule-based view stops changing on session
churn *at all*. Verified by enumerating every writer of `views`:

| Writer | Location | Fires on | With rules |
|---|---|---|---|
| User edit (add/remove session to view) | `patch_settings` via PWA | Every membership change | **Never fires** — membership is not stored |
| Stale-key pruning | `main.py:604-645`, `save_settings()` direct | Every ~2s cycle, whenever a key ages out | **Never fires** — rules are not keys (§7.3) |
| Key normalization (bare → `device_id:name`) | `main.py:508-518`, `save_settings()` direct | Whenever a bare-name entry exists | **Never fires** — patterns are not keys, and `normalize_session_keys` only walks `view["sessions"]` |
| Federation sync | `apply_synced_settings` | On receipt of a newer peer blob | Unchanged |
| Rule edit | `patch_settings` | When a human changes a pattern | The only remaining writer |

So for a fully rule-based view the write frequency drops from *"every session lifecycle event on
any device in the fleet"* to *"when a human edits the rule."* Every write avoided is one fewer
LWW arbitration, one fewer chance for a stale copy to be the freshest, and one fewer settings-
history snapshot burning a slot in the 20-deep ring.

### 7.2 An observation found while verifying, worth recording (no change requested here)

Both background writers above (`main.py:518` normalization, `main.py:645` pruning) call
`save_settings()` **directly**, not `patch_settings()`, so **neither advances
`views_updated_at`.** A pruned/normalized `views` array therefore carries a *stale* arbitration
timestamp and is comparatively undefended in the next LWW round.

This is pre-existing, is not caused by this feature, and this spec deliberately does **not**
change it — bumping the timestamp from a background loop would make every device's janitorial
pass win view arbitration fleet-wide, which is very likely worse. It is recorded because it is
the mechanism that makes §7.1's claim matter: rules do not merely reduce write *volume*, they
eliminate the two writers that mutate `views` **without** updating the timestamp that arbitrates
it. File separately if it deserves attention.

### 7.3 Rules never enter the pruning ledger

`prune_stale_keys` (`views.py:456`) walks `hidden_sessions` and `view["sessions"]` only. A
pattern is not a key, has no owning device, never goes missing, and never accrues
`first_missed_at` time in `pruning.json`. **This is the mechanical reason a rule-based view
cannot decay** — and it is directly assertable in a test (§11.4 step 5).

Pins inside a rule-bearing view continue to prune exactly as today. That is correct and
deliberate: a pin is a manual assertion about a specific session and should still expire when
that session is positively known to be gone.

### 7.4 Mixed-version fleets

- **New device → old peer.** The old peer stores `match_names` verbatim (§0.5), ignores it, shows
  the view's pins only, and returns it unchanged on the next sync. No data loss, no crash, no
  schema bump.
- **Old device → new peer.** A view with no `match_names` is a manual view. Byte-identical
  behavior.
- **No `_schema_version` bump.** Nothing about the v1↔v2 hidden-state contract changes, and
  bumping it would make old peers believe something changed that did not.

---

## 8. Migration and back-compat

**There is no migration.** No one-shot upgrade, no data rewrite, no version gate.

| Situation | Behavior |
|---|---|
| Existing manual view, untouched | Byte-identical to today, on every surface |
| Existing manual view, user adds a rule | Pins keep working; matches are added (union). No write beyond the rule itself. No backstop risk (§0.4) |
| New rule-only view (`sessions: []`) | Works. `sessions` may be `[]` or absent |
| User later deletes the rule | View reverts to its pins. If it had none it is honestly empty — same as any empty manual view today |
| Pre-feature client PATCHes a rule-bearing view back | The client round-trips whatever it read; `match_names` is inside the object it echoes, so it survives. If a client *constructs* view objects field-by-field it would drop the rule — the backstop's new pattern-counting (§3.4) is what catches a fleet-wide instance of that |
| Older muxplex peer in the fleet | §7.4 |

**API back-compat.** Every change is additive: one new optional key inside `views`, one new
optional field on session dicts, one new endpoint, one new 400 discriminator. No existing field
changes shape or meaning. No endpoint changes its response shape. A client that ignores all of it
behaves exactly as it does today for manual views.

---

## 9. Frontend (this repo)

### 9.1 `frontend/app.js` — membership becomes a lookup

**`filterVisible()` (`app.js:1073`), user-view branch only.** Replace the dual-key search with
the server's answer, and **delete the now-dead `members` local** (leaving it as unused state is
exactly the kind of stale second source of truth this change exists to remove):

```js
var userView = /* unchanged lookup */;
if (!userView) return [];          // keep: the documented "unknown view -> empty" contract
function inView(s) {
  return (s.views || []).indexOf(view) !== -1;
}
```

**No fallback to the old client-side matching, deliberately.** The PWA is served by the server
that annotates, with `Cache-Control: no-cache` specifically to guarantee the deployed JS is the
server's own (AGENTS.md, "the no-cache header is load-bearing"), and the local server annotates
*remote* sessions too (§5.3) — so the annotation is present regardless of any peer's version. A
missing `s.views` is therefore a server bug, and a silent dual path would hide it. Provenance
(pinned vs matched) reads `view.sessions` at its own call sites (§9.3), not here.

`isHidden()` and the hidden branch are unchanged. `"all"` is unchanged. `visibleCount()`,
`getVisibleSessions()`, the dropdown counts (`app.js:1495`, `:1587`), the Views settings tab
counts (`app.js:1859`), and the grid summary (`app.js:3095`) all flow through `filterVisible` and
need no changes of their own.

### 9.2 Error surfacing — copy the Commands treatment exactly

Add, mirroring `loadSessionCommands()` (`app.js:3665`) and its render/badge pair:

- Module state `_viewRuleErrors = []`, populated by a new `loadViewRules()` that fetches
  `GET /api/views`. Called from the same two places `loadSessionCommands()` is (page load and
  every `openSettings()`), **plus** from `followRemoteViewDefinitions()`'s existing
  settings-changed branch — so a rule that arrives by federation sync or a file edit surfaces
  without a reload.
- `index.html`: a `Views` tab badge `<span id="settings-tab-view-errors-badge" class="error-count-badge hidden">` on the
  existing Views tab button (mirror line 167), and inside the Views tab an error section
  `settings-view-errors-field` / `settings-view-errors` mirroring `settings-command-errors-field`
  / `settings-command-errors` (lines 292-294), `style="display:none"` by default.
- `_updateCommandErrorBadges()` (`app.js:4711`) generalizes to `_updateConfigErrorBadges()`: the
  two **gear** badges (`settings-error-badge`, `settings-error-badge-expanded`) show
  `_sessionCommandErrors.length + _viewRuleErrors.length`; each **tab** badge shows only its own
  source. Keep the old function name as a thin caller **or** update its callers — but see the
  AGENTS.md warning in §11.6 about `test_frontend_js.py` source assertions before renaming.

### 9.3 Manage View / view pickers — rule-matched membership is honest and non-removable

Three places let a user toggle a session's view membership: the flyout picker
(`app.js:2621`), the mobile picker (`app.js:2474`), and "remove from current view"
(`app.js:2811`). Each computes `isIn` from `(v.sessions || []).indexOf(sessionKey) !== -1`.

Change each to distinguish the two states, using data already on hand — no new server field:

```js
var pinned  = (v.sessions || []).indexOf(sessionKey) !== -1;
var matched = (session.views || []).indexOf(v.name) !== -1 && !pinned;
```

- `pinned` → today's behavior exactly (toggle adds/removes).
- `matched` → render as a member, **disable the toggle**, and label it (e.g. `matched by rule`
  with the matching pattern as a title/tooltip). Do **not** offer a control that would silently
  do nothing — that is the one fiddly consequence of the union (§2.2) and hiding it is worse than
  showing it.
- Manage View's member list (`manage-view-list`, `index.html:136`) shows the same distinction.

**Rule editing UI — minimum viable, and deliberately minimal.** In the Manage View panel, one
`<textarea>`, **one pattern per line**, blank lines ignored, saved through the existing
`patchSettingsGuarded()` path (so CAS and backstop handling come for free) as
`view.match_names`. Newline-separated, not comma-separated: tmux permits commas in session names,
newlines it does not. On a 400 `invalid_view_rule`, render `errors[]` inline in the panel rather
than a generic toast. A richer editor (pattern chips, live match preview) is deferred — the
preview is genuinely useful and genuinely a second design; it is not needed to ship the
mechanism.

### 9.4 `frontend/deck/deck.js` — counts from the annotation

`viewSessionCounts()` (`deck.js:1250`) currently re-derives membership by `":<name>"` suffix
matching against `settings.views[].sessions`. That derivation becomes wrong for rule views.

- Keep the full `/api/sessions` payload alongside `allSessionNames` in `poll()` (`deck.js:1794`)
  — it is already fetched and already carries the annotation.
- User-view counts become `sessions.filter(s => (s.views || []).indexOf(name) !== -1).length`.
- The `hidden` count keeps today's suffix matching against `settings.hidden_sessions` (hidden has
  no rules and gains none), and `all` stays `allSessionNames.length`.
- `loadViewCounts()` (`deck.js:2329`) keeps its `/api/settings` fetch for `hidden_sessions` only,
  and keeps its best-effort no-fabrication contract.
- The soft deck's **session list** is unchanged — it already comes from `GET /api/view`.

---

## 10. Client library and the cross-repo gap

### 10.1 `client/muxplex_client` (this repo) — ships now

- `models.py:53` `Session`: add `views: tuple[str, ...] = ()`. Defaulted, so every existing
  construction site keeps compiling.
- `_protocol.py:65` `parse_session()`: add
  `views=tuple(raw.get("views") or ())` — tolerant of a pre-feature server that omits the field.
- **Do not** add `views` to `ViewSession` (§4 item 2).
- `client/tests/test_protocol.py`: a session payload with `views` parses; one without yields `()`.
- `muxplex/tests/test_client_contract.py`: assert the server's `GET /api/sessions` payload
  satisfies the parser (this is the test that keeps the vendored copy honest).

### 10.2 muxplex-deck (separate repo) — the known gap from §0.7

`muxplex-deck/src/muxplex_deck/views.py` `resolve_view()` ports `filter_visible` and is called at
`main.py:563`. Until it is updated, **a rule-based view renders empty on the hardware deck**;
manual views are unaffected.

The remedy, once `muxplex_client` (§10.1) is released — in `_member_matches`'s caller, prefer the
server's answer and keep today's logic as the pre-feature fallback:

```python
if session.views:                       # new server: authoritative
    return active_view in session.views
return _member_matches(session.name, members)   # pre-feature server: unchanged
```

This fallback **is** legitimate — unlike §9.1's — because that repo ships and versions
independently and must interoperate with servers on both sides of this change. Track it as a
follow-up issue in muxplex-deck; it is not a blocker for this repo, and this spec's `views`
annotation is what makes it a five-line change instead of a port of the matcher.

---

## 11. Test plan

Run inside a DTU: `make test`. Commit first — `git archive HEAD` is what the DTU tests
(AGENTS.md). Never on a host running a live muxplex.

### 11.1 `muxplex/tests/test_views.py` — the matcher and resolution (unit)

- `matches_name_pattern`: `amplifier-*` matches `amplifier-foo`, not `foo-amplifier`; `*-test`
  matches `x-test`; case-insensitivity both directions (`AMPLIFIER-*` vs `amplifier-foo` and
  vice versa); literal `foo` matches only `foo`; `?` and `[abc]` classes behave; non-`str` inputs
  return `False` without raising.
- **Platform determinism**: assert `matches_name_pattern("Foo", "foo")` is `True` — the assertion
  that fails if someone "simplifies" to `fnmatch.fnmatch` (which would pass on macOS and fail on
  Linux). Comment it as such.
- `view_patterns`: drops non-list `match_names`, non-str entries, empty strings, and any pattern
  containing `":"`; preserves file order; absent key → `[]`.
- `filter_visible` with rules: rule-only view; pins-only view (regression, unchanged); union
  (a pinned session that does not match, and a matching session that is not pinned, both
  present, no duplicates); a hidden session that matches a rule is excluded unless
  `include_hidden=True`; a session with a truthy `status` is never a member; `"all"`/`"hidden"`
  unaffected by rules.
- `view_names_for_session` / `annotate_view_membership`: order follows `settings["views"]`;
  status entries get `[]`; **input dicts are not mutated** (assert the input list's dicts have no
  `views` key afterward — this is the `_federation_cache` guard from §5.1).
- `validate_view_rules`: one case per R1–R4, each asserting the *content* of the message
  (the `':'` message must name the reason); a clean config returns `[]`; a non-dict entry in
  `views` produces no error (§6.3).
- `_view_member_count` / `assess_views_destruction`: patterns count as members; a config with no
  `match_names` produces identical counts to today (regression); stripping every pattern from a
  multi-view config is assessed **destructive**.

### 11.2 `muxplex/tests/test_settings.py` — write paths

- `patch_settings` with a malformed rule raises `InvalidViewRuleRejected` and **writes nothing** —
  assert the on-disk file is byte-identical, including an unrelated key in the same patch.
- `patch_settings` with a valid rule writes it and bumps `views_updated_at`.
- `apply_synced_settings` with a malformed rule **stores it and does not raise** (§5.2), and the
  view still resolves to its pins.
- Round-trip: `match_names` survives `save_settings` → `load_settings` (§0.5).
- Backstop interaction: a patch that adds a rule to a 20-pin view without removing pins is **not**
  destructive.

### 11.3 `muxplex/tests/test_api.py` — the wire

- `GET /api/sessions`: every entry carries `views`; a rule-matching session lists the view; a
  non-matching one does not; a manual-only config is unchanged apart from the new key.
- `GET /api/federation/sessions`: local **and** remote sessions are annotated; status tiles carry
  `views: []`; and — the cache guard — after one call with a rule view, mutate settings to remove
  the rule and assert the next call reflects it (proves nothing was baked into
  `_federation_cache`).
- `GET /api/view` with a rule-based `active_view`: resolves correctly with **no handler change**;
  `?sort=attention` still applies; `views:` cycle list unchanged; entries do **not** carry `views`.
- `GET /api/views`: shape; invalid patterns absent from `match_names` and present in both the
  per-view and flat `errors`; a clean config returns `errors: []`; requires auth.
- `PATCH /api/settings` with a malformed rule → **400**, `invalid_view_rule: true`, `errors[]`
  non-empty, and a subsequent `GET /api/settings` proves no write happened.

### 11.4 `muxplex/tests/test_auto_views_integration.py` — **the self-healing proof, real tmux**

New module, `@pytest.mark.integration`. Follow `test_integration.py`'s fixture pattern exactly:
an isolated server on a **unique named socket** (`tmux -L auto-views-test`), torn down with
`tmux -L auto-views-test kill-server` — never a bare `kill-server`, never a name-matched kill
(AGENTS.md §"Two ways to destroy every live tmux session"). Reuse
`make_run_tmux_for_socket()` and drive the API through `TestClient(app)`. `SETTINGS_PATH` is
already redirected to tmp by `conftest.py`'s autouse rail.

> **This module MUST add its own autouse fixture redirecting
> `muxplex.pruning.PRUNING_STATE_PATH` to `tmp_path`** — copy `test_pruning.py:22-30`'s fixture
> verbatim. `conftest.py`'s rails cover `SETTINGS_PATH` but **not** this path. Step 5 below reads
> and the poll cycle writes `pruning.json`, and on a developer box the real
> `~/.config/muxplex/pruning.json` is — per AGENTS.md's recovery section — the **only record of
> lost session names** after an incident. Clobbering it is the exact class of "a test that
> destroys its host still passes" failure `conftest.py` exists to stop.

The sequence, which is the actual evidence the feature works:

1. Create `av-alpha` and `unrelated-one` on the isolated socket.
2. Write settings: one view `Auto` with `match_names: ["av-*"]` and `sessions: []`. Set
   `active_view = "Auto"`. **Record `views_updated_at` and the exact on-disk `views` JSON.**
3. Run `_run_poll_cycle()`. `GET /api/view` → exactly `["av-alpha"]`. `GET /api/sessions` →
   `av-alpha.views == ["Auto"]`, `unrelated-one.views == []`.
4. `tmux -L auto-views-test new-session -d -s av-beta`; run `_run_poll_cycle()`.
   `GET /api/view` → `["av-alpha", "av-beta"]`.
   **Assert `views_updated_at` is unchanged and the on-disk `views` JSON is byte-identical.**
   *This assertion is the point of the test:* the view healed without a settings write, which is
   what a hand-curated list can never do.
5. `tmux -L auto-views-test kill-session -t av-alpha`; run `_run_poll_cycle()` (twice, to clear
   the ~2s cache). `GET /api/view` → `["av-beta"]`. **Assert `views_updated_at` unchanged, on-disk
   `views` byte-identical, and `pruning.json`'s `first_missed_at` contains no entry for
   `av-alpha`** — a rule-matched session never enters the pruning ledger (§7.3), which is the
   mechanical reason it cannot rot.
6. Contrast arm, same test file: a **pinned** `av-alpha` in a second view *does* get a
   `first_missed_at` entry after step 5 — proving the two mechanisms are genuinely different and
   that the assertion in step 5 is not vacuous.
7. Union arm: pin `unrelated-one` into `Auto`; poll; assert `Auto` = `["av-beta",
   "unrelated-one"]` with no duplicates, and that `unrelated-one.views == ["Auto"]`.

### 11.5 `frontend/tests/*.mjs` (node suite — run with the glob, never a single file)

- `filterVisible` reads `s.views` for user views; `"all"`/`"hidden"` unchanged; a session missing
  `views` is **not** a member (asserting the deliberate absence of a fallback, §9.1).
- Provenance: pinned vs matched renders differently and the matched toggle is disabled.
- `_updateConfigErrorBadges`: gear badges show the **sum**; each tab badge shows its own source;
  zero errors hides all of them.
- `viewSessionCounts` (deck) counts from the annotation and matches the server's own count for
  the same fixture.

### 11.6 Two existing-test hazards to expect

- **`test_frontend_js.py` asserts on JS source text** (229 regex assertions). Renaming
  `_updateCommandErrorBadges` or restructuring `filterVisible` will trip it. AGENTS.md's rule
  applies: ask whether the *behavior* changed; if not, fix the assertion to follow the new
  structure (assert the delegation **and** the delegate) — never loosen it to pass.
- **`tests/test_shared_scope.mjs`** covers any new frontend script automatically. This work adds
  no new `<script>` tag, but every new top-level binding in `app.js`/`deck.js` must still be
  uniquely named across all classic scripts (`_viewRuleErrors`, not `_errors`).

---

## 12. Success criteria

A builder is done when all of these hold:

1. A view with `match_names: ["av-*"]` and no pins shows exactly the matching live sessions on:
   the PWA grid, the PWA view-dropdown count, the Views settings tab count, `GET /api/view`, the
   soft-deck session list, and the soft-deck picker count.
2. Creating and killing a matching tmux session changes what the view contains **without any
   write to `settings.json`** — proven by §11.4 steps 4–5 (`views_updated_at` unchanged, file
   byte-identical, no `pruning.json` entry).
3. A view with both pins and a rule shows the union, with no duplicates, and the pinned member
   still removable while the rule-matched member is visibly non-removable.
4. `PATCH /api/settings` with `match_names: ["bad:pattern"]` returns 400 with
   `invalid_view_rule: true`, writes nothing, and the error names the `':'` reason.
5. The same malformed pattern arriving by federation sync or a hand edit of `settings.json` is
   stored, matches nothing, appears in `GET /api/views`'s `errors[]`, logs at `error`, and shows a
   count badge on the Settings gear without opening Settings.
6. A config with no `match_names` anywhere produces byte-identical behavior on every endpoint and
   every surface, apart from the additive `views` field on session payloads.
7. `grep -rn "fnmatch" muxplex/frontend/` returns nothing. The matcher exists in exactly one
   place in this repo's runtime code besides the `/input` fence.
8. `uv run pytest` green in a DTU; `node --test frontend/tests/*.mjs` green; `test-latest-deps`
   green.

---

## 13. Out of scope — do not build as part of this

- Exclusions (§2.3), device rules (§2.8), activity-window rules, cwd rules, regex rules.
- Attention views (§2.6) and set composition (§2.7) — **rejected**, not merely deferred.
- Rules on `hidden_sessions`. `hidden` stays a rule-free, explicitly-curated list.
- Extending `GET /api/view` to federated sessions.
- A live "these sessions match" preview in the rule editor (useful; a separate design).
- Fixing `muxplex config set`'s silent-success on fenced keys (pre-existing;
  `docs/plans/2026-08-02-named-session-command-pairs-plan.md` §0.4 already files it).
- Making the background `views` writers advance `views_updated_at` (§7.2) — recorded, not
  changed, deliberately.
- Retiring `test_frontend_js.py`'s source-scraping assertions (AGENTS.md: "a project, not a
  cleanup").

---

## 14. Documentation to update in the same PR

- **`docs/API_SEMANTICS.md`** — a new bullet in the "semantics external clients re-implement"
  list: session dicts carry `views` (the server's resolved user-view membership, pins ∪ name-glob
  rules); clients MUST read it rather than re-deriving membership from `settings.views`, with the
  §0.1 finding as the incident-shaped rationale; `GET /api/views` as the canonical
  resolution+errors endpoint (cross-reference `GET /api/session-commands`); `invalid_view_rule:
  true` added to the discriminator convention alongside `backstop` / `terminal_conflict` /
  `unknown_command_id`; and the §2.1 note that glob patterns match the **bare name** because the
  key qualifier is a UUID.
- **`AGENTS.md`** — one short section: rules are matched with `casefold()` + `fnmatchcase` and are
  **deliberately a separate implementation** from `terminal_input.session_matches_allowlist`
  (§2.1); and the standing prohibition from §2.5 — *the server never materializes rule matches
  into `view["sessions"]`*.
- **`docs/AGENT_GUIDE.md`** — one paragraph so an agent can create a self-maintaining view.
- **`README.md`** — the `match_names` key in the settings reference, with the one-syntax
  explanation.
- **`docs/BACKLOG.md`** — delete item 1 (the backlog's own rule: an item that graduates is
  deleted, not annotated), and move this spec to `docs/plans/<date>-auto-views-plan.md`.
- **`CHANGELOG.md` / version bump: NO.** Release-time, owner-only (AGENTS.md).

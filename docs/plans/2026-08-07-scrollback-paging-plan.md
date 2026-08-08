# Scrollback paging for on-demand pane capture

**Status:** design only, not implemented. Written against `main` at v0.42.0 (`a586c72`).
**Scope:** `GET /api/sessions/{name}`, `sessions.capture_pane`, `sessions.enumerate_sessions`,
`muxplex-client`. Every tmux claim below was **measured on tmux 3.4** against an isolated
scratch socket, not read off a man page. Commands and outputs are quoted in §2.

---

## 0. Read this first — what should NOT be built

Four things a reader of the problem statement would reasonably reach for. Each is
rejected here with a reason, so nobody spends a PR discovering it.

### 0.1 Do NOT build a paged scrollback viewer in the PWA

**The web terminal already has unbounded scrollback, and it is not this API.**
`WS /terminal/ws` relays ttyd, which is `tmux attach` — a real tmux client. muxplex's
managed config sets `set -g mouse on` (`muxplex/tmux_templates/base.conf:27`), so
scrolling up in the browser enters **tmux copy-mode**, which reads tmux's own history
directly, to whatever depth the pane retains. Zero muxplex code is involved and there is
no 2000-line wall there today.

`terminal.js:463`'s `scrollback: mobile ? 500 : 5000` is xterm.js's *client-side* buffer
of bytes streamed since attach. It is a rendering convenience, unrelated to tmux history,
and raising it does not reach older output either.

Building an HTTP-paged scrollback pane in the PWA would be a **third** renderer of
terminal content (after the live terminal and the grid-tile preview), competing with the
live terminal that already does the job better, with worse fidelity (`capture-pane -e`
text vs. a real terminal emulator). **The UI and the agent API deliberately get different
mechanisms, and that is the answer, not a gap.**

The one honest caveat: copy-mode is only reachable in the *live terminal* view, and a TUI
that captures mouse events (amplifier's own TUI) intercepts the scroll. That is a
tmux/TUI concern; paging an HTTP endpoint does not fix it.

### 0.2 Do NOT add a cursor/token system

The problem statement explicitly permits "raise the cap and add `-S`/`-E` passthrough" as
an acceptable answer. It is *close* to the answer, but not quite: raw tmux coordinates
drift under a live pane (§2.2, measured) and `capture-pane` **clamps out-of-range
requests silently** (§2.4, measured), which violates the no-silent-truncation constraint
at the tmux layer, below anything muxplex can see.

What is needed is one integer conversion the server performs (§3.2) and three booleans
it reports. That is not a cursor system — there is no opaque token, no server-side
session, no expiry, nothing to store. **Ship the integer, not the cursor.**

### 0.3 Do NOT add rate limiting, request budgets, or a paging quota

Measured (§2.5): `capture-pane` cost is **O(window requested), not O(depth)**. A 10-row
window 40,000 lines back costs 0.00s and 500 bytes — the same as a 10-row window at the
live end.

The existing `MAX_CAPTURE_LINES = 2000` is a **window-size** cap, and it already bounds
the only thing that costs anything. An agent can issue 25 requests at `lines=2000` today,
right now, with no paging. Paging does not raise the per-request ceiling and does not
raise the achievable request rate. **It only makes each of those 25 requests return
*different* 2000 lines instead of 25 copies of the same ones.** The DoS surface is
unchanged in magnitude and strictly improved in value.

Adding a budget would be new machinery guarding a risk that exists identically today.
If per-caller rate limiting is ever wanted, it belongs at the auth middleware for the
whole API, not bolted onto one endpoint by this feature.

### 0.4 Do NOT raise `MAX_CAPTURE_LINES`

It stays at **2000**, unchanged. It is a mirrored constant in `muxplex-client`
(`client/muxplex_client/constants.py:26`) pinned by `test_client_contract.py:310`; leaving
it alone means this feature adds **zero** new drift surface. Paging is what removes the
reason to raise it: depth becomes unbounded while the per-request window stays exactly
where it is.

---

## 1. Prerequisite bug: the retention guarantee does not exist

**This must be resolved before or with paging, because paging pages *toward* it.**
`ensure_history_retention()` (`sessions.py:486`) does not do what three separate documents
say it does.

### 1.1 The claim

- `sessions.py:452-462` — "tmux `history-limit` applied to every session muxplex creates
  … Deliberately set well above `MAX_CAPTURE_LINES`" → `SESSION_HISTORY_LIMIT = 5000`
- `main.py:1415` — "Sessions are created with their tmux `history-limit` raised well above
  `MAX_CAPTURE_LINES` … specifically so a max-depth request has real backing data instead
  of tmux's own, possibly much lower, default silently truncating it."
- `docs/AGENT_GUIDE.md:972-975` — "Sessions also get their tmux `history-limit` raised to
  5000 on creation specifically so a max-depth request has real scrollback behind it."

### 1.2 The measurement

`history-limit` is a **session option that binds a pane at pane-creation time.** Setting
it on a session whose pane already exists changes nothing about that pane.
`spawn_session_command()` calls `ensure_history_retention(name)` at `sessions.py:653` —
*after* the template has already created the session and its pane.

```
# set history-limit to 50 on a session whose pane already exists, then emit 500 lines
$ tmux set-option -t beh history-limit 50
$ tmux send-keys -t beh 'seq 1 500' Enter
$ tmux display-message -p -t beh 'history_limit=#{history_limit} history_size=#{history_size}'
history_limit=50000 history_size=481           <- option ignored by the live pane
$ tmux capture-pane -p -t beh -S - | wc -l
505

# a window created AFTER the set-option does inherit it
$ tmux set-option -t sat history-limit 60 ; tmux new-window -t sat -d -n small
$ tmux display-message -p -t sat:small 'history_limit=#{history_limit}'
history_limit=60
$ tmux send-keys -t sat:small 'seq 1 500' Enter ; sleep 2
$ tmux capture-pane -p -t sat:small -S - | head -1
423                                             <- lines 1-422 evicted
```

So `ensure_history_retention()` is a **no-op on every pane it is meant to protect.**

### 1.3 What panes actually get

| Host | Global `history-limit` | Source |
|---|---|---|
| `muxplex tmux install` run | **50000** | `muxplex/tmux_templates/base.conf:28` — `set -g history-limit 50000` |
| No muxplex tmux config, no user config | **2000** | tmux compiled-in default (verified: `tmux -f /dev/null` → `history_limit=2000`) |

Two consequences, both bad:

1. **On an unmanaged host, retention is exactly `MAX_CAPTURE_LINES`.** Today's deepest
   legal request (`?lines=2000`) already sits on the retention boundary, and tmux clamps
   silently (§2.4) rather than saying so — the precise failure the `main.py:1415` comment
   claims to prevent.
2. **On a managed host, `SESSION_HISTORY_LIMIT = 5000` is a 10× *reduction*** — in the one
   case it binds (a window created later inside a muxplex-created session), it drops
   50000 → 5000.

### 1.4 Recommendation

**Delete `SESSION_HISTORY_LIMIT` and `ensure_history_retention()`, and report the real
value instead.** Nothing is lost — the call never bound a pane. Retention policy already
lives, and correctly applies, in the managed tmux config (`base.conf:28`), which is
evaluated at pane creation, which is the only moment it can work.

**Explicitly rejected: `tmux set-option -g history-limit N` from the server at
startup.** It would work (global option, applies to panes created afterward), and someone
will propose it. It is wrong for this repo: `tmux_config.py`'s module docstring states
the posture in as many words — *"This is deliberately the opposite of the conda/rustup/nvm
convention: they install last because they want to win. We install first because we want
to lose."* A runtime `set -g` write would silently outrank the user's own `~/.tmux.conf`,
which is exactly the thing that design refuses to do.

The honest substitute is to **surface `history_limit` per session** so a caller can see
"this pane retains 2000 lines" rather than being told 5000 and discovering otherwise by
getting a short page. That is free (§2.6) and is part of this design.

**Doc corrections required in the same PR:** `sessions.py:452-462`, `main.py:1415`,
`docs/AGENT_GUIDE.md:972-975`.

---

## 2. What tmux actually guarantees (all measured, tmux 3.4)

### 2.1 The coordinate system

`capture-pane -S/-E` are relative to the **top of the visible screen**: line `0` is the
first visible row, negative numbers are history, `-` means "start of history" (`-S`) or
"end of visible" (`-E`). Valid range is `[-history_size, pane_height - 1]`.

`capture_pane()` today passes only `-S -{lines}`, so `-E` defaults to the bottom of the
visible screen. **That is why `?lines=N` returns `N + pane_height` rows, not `N`** —
measured: `-S -30` → 80 rows and `-S -2000` → 2050 rows on a 50-row pane. This is
existing, shipped behavior and the design preserves it exactly (§3.4).

### 2.2 Relative coordinates drift — naive offset paging is broken

Identical coordinates, 31 lines of output apart, returning different text:

```
H1=381
$ tmux capture-pane -p -t anchor -S -100 -E -91
278 279 280 281 282 283 284 285 286 287

$ tmux send-keys -t anchor 'seq 9000 9029' Enter    # 31 lines scroll in
H2=412   (delta=31)

$ tmux capture-pane -p -t anchor -S -100 -E -91     # SAME coordinates
309 310 311 312 313 314 315 316 317 318             # DIFFERENT content
```

A client that pages by decrementing `-S`/`-E` **skips** exactly as many lines as arrived
between requests. Silent data loss, invisible to the caller. This is the reason
`-S`/`-E` passthrough is not sufficient on its own.

### 2.3 `history_size + relative` is a stable absolute anchor

The same measurement, with the offset corrected by the observed `history_size` delta:

```
$ tmux capture-pane -p -t anchor -S -131 -E -122     # (-100-31, -91-31)
278 279 280 281 282 283 284 285 286 287              # byte-identical to T1
```

**Define `abs = history_size + rel`,** where `abs = 0` is the oldest retained row.
Absolute indices name the same text across requests as long as nothing has been
**evicted** — see §2.4.

### 2.4 Saturation evicts, and clamping is silent

Once `history_size` reaches `history_limit`, the oldest rows are dropped permanently
(measured in §1.2: limit 60, oldest surviving line `423`). `history_size` then **pins** at
the limit while content keeps scrolling — so the origin of the absolute index slides
forward and old absolute coordinates shift. No coordinate system recovers evicted lines;
the honest answer is "this is all there is."

`history_size` therefore doubles as an eviction detector: it grows monotonically until the
first eviction and is pinned at `history_limit` from then on. **`saturated =
history_size >= history_limit`** is the whole rule, and the server resolves it (§3.3).

Out-of-range requests are **clamped with exit 0 and no diagnostic**:

```
$ tmux capture-pane -p -t probe -S -1000     # history_size = 0
(24 rows — the visible screen)  rc=0

$ tmux capture-pane -p -t beh -S -999999 | wc -l ; tmux capture-pane -p -t beh -S - | wc -l
505
505

$ tmux capture-pane -p -t beh -S -5 -E 999 | wc -l ; tmux capture-pane -p -t beh -S -5 -E 23 | wc -l
29
29
```

**tmux will never tell you it truncated your request.** muxplex must compute the clamp
itself, from `history_size`, and report it. This is the load-bearing reason the server
converts coordinates rather than passing them through.

### 2.5 Cost is O(window), not O(depth)

Pane with 49,955 lines of history at 200 columns:

| Request | Wall time | Bytes | Rows |
|---|---|---|---|
| `-S -30` | 0.00s | 4 KB | 80 |
| `-S -2000` | 0.00s | 100 KB | 2050 |
| `-S -10000` | 0.03s | 500 KB | 10050 |
| `-S -50000` | 0.10s | 2.5 MB | 50005 |
| **`-S -40000 -E -39991`** (10 rows, 40k deep) | **0.00s** | **500 B** | **10** |

This is the fact that makes paging correct rather than a workaround: **a bounded window
is cheap at any depth.** The current single cap conflates depth with cost; only window
size costs anything, and the window cap already bounds it.

### 2.6 Depth metadata is free

`history_size`, `history_limit`, `alternate_on`, and `pane_height` are all available from
`list-sessions -F` — **the same call `enumerate_sessions()` already makes every poll
cycle**:

```
$ tmux list-sessions -F '#{session_name}	#{window_activity}	#{session_created}	#{pane_current_path}	#{history_size}	#{history_limit}	#{alternate_on}	#{pane_height}'
beh	1786158850	1786158849	/home/…	481	50000	0	24
probe	1786158801	1786158799	/home/…	103	50000	0	24
```

Zero additional subprocess round trips. Like `window_activity` and `pane_current_path`
already on that line, these resolve against the session's **active window's active pane**
— the same pane `capture_pane()` reads, so no new inconsistency is introduced.

### 2.7 Metadata and capture can be read atomically

Two tmux commands in **one invocation** share the server's command loop, so they observe
the same grid state — no race between reading `history_size` and capturing:

```python
# argv form, exec (no shell); ';' is its own argv element
await run_tmux(
    "display-message", "-p", "-t", name, "#{history_size}\t#{pane_height}",
    ";", "capture-pane", "-e", "-p", "-t", name, "-S", str(s), "-E", str(e),
)
# -> '195\t10\n190\n191\n'   (metadata line, then the capture)
```

Verified via `asyncio.create_subprocess_exec` exactly as `run_tmux()` invokes tmux. This
is what lets the server report `start` truthfully instead of approximately.

### 2.8 Alternate screen (a TUI holding the pane) — honest limitation

While `alternate_on=1` (vim, less, **amplifier's own TUI** — muxplex's primary workload),
the default capture returns the **TUI's screen** for the visible region but **pre-TUI
normal-screen content** for the history region:

```
in-TUI: alternate_on=1 history_size=282
$ tmux capture-pane -p -t alt | tail -3          # visible: less's display
22
23
:
$ tmux capture-pane -p -t alt -S -20 -E -18      # history: pre-TUI shell output
259
260
261
```

There is a **content discontinuity at the visible/history boundary** whenever a TUI holds
the pane. This is tmux's model, not a muxplex bug, and it is not fixable here — but a
caller paging into history behind a TUI deserves to know. Surfacing `alternate_screen`
is a small additive field (§3.3, Phase 2) rather than leaving the caller to be quietly
confused.

---

## 3. Design

One additive query parameter and four additive response fields on the endpoint that
already exists. No new endpoint, no new constant, no new state.

### 3.1 Coordinate contract

```
history_size (H)  rows currently in history       ] read atomically
pane_height  (P)  rows on the visible screen      ]   with the capture (§2.7)

total = H + P              total addressable rows RIGHT NOW
abs                        0 = oldest retained row … total-1 = newest row
rel = abs - H              the tmux -S/-E coordinate
```

Absolute indices are the **server's** coordinate system, not tmux's. The server converts
on every request using an `H` read in that same request, which is what makes them stable
against new output (§2.3) without asking the client to know anything about tmux.

### 3.2 Request

```
GET /api/sessions/{name}?lines=N              # unchanged — the live end
GET /api/sessions/{name}?lines=N&before=<abs> # the N rows immediately OLDER than <abs>
```

`before` is **exclusive** and additive. Omitting it is byte-identical to today, down to
the `+ pane_height` quirk (§2.1) — the existing `capture_pane(name, lines)` call is used
unchanged on that path.

Backward paging is the only motion that exists, because it is the only motion anyone
performs: an agent recovering a build log and a human scrolling up both read from the
present toward the past. `before=` expresses it in one parameter and lets the **server**
own the end-of-history clamp; `start=` would have forced every client to write
`max(0, prev_start - lines)` and then to correctly not read the short final page as
truncation. Forward paging is reachable by paging backward to `has_more: false` and
reversing, and has no demand.

Validation, matching the endpoint's existing no-silent-clamp discipline
(`docs/AGENT_GUIDE.md:967`):

| Condition | Result |
|---|---|
| `lines` outside `[1, 2000]` | **400** — unchanged message and behavior |
| `before < 0` | **400** — `"before must be between 0 and {total} (got …)"` |
| `before > total` | **400** — same message; `total` only grows while unsaturated, so this means a client bug or a saturation-era shift |
| `before == 0` | **200** with an empty page, `row_count: 0`, `has_more: false` — you reached the beginning. A 4xx here would be a lie |

### 3.3 Response

Existing keys keep their exact current meaning. `lines` in particular **still echoes the
requested depth**, not the row count — redefining it would be a silent semantic change to
a shipped field, which `AGENTS.md` forbids.

```jsonc
{
  "name": "agent-build",              // existing
  "snapshot": "…",                    // existing
  "lines": 500,                       // existing — depth REQUESTED (unchanged meaning)
  "bell": { … },                      // existing
  "last_activity_at": 1753500123.0,   // existing

  "start": 49024,                     // NEW — absolute index of the first returned row
  "row_count": 500,                   // NEW — rows actually returned
  "total": 50024,                     // NEW — H + P, addressable range at capture time
  "has_more": true,                   // NEW — is there anything older than `start`?
  "saturated": false                  // NEW — history_size >= history_limit
}
```

The next page is always `?before={start}` — that is the entire client-side rule.

**Why `has_more` when it is `start > 0`.** `AGENTS.md`'s standing rule: resolve a
client-facing rule server-side rather than shipping it to each of PWA / sidecar / agents.
An off-by-one in three independently-written clients is exactly the drift this repo has
been bitten by; the boolean costs one byte on the wire.

**Why `saturated` is the field that satisfies "no silent truncation."** The two ways a
page can be the last one are genuinely different, and conflating them is the lie the
constraint forbids:

| `has_more` | `saturated` | Meaning |
|---|---|---|
| `false` | `false` | You reached the **true beginning** of this session's output. |
| `false` | `true` | You reached the **retention wall**. Older output existed and is gone. |
| `true` | `true` | More is available, **and** absolute indices may have shifted since your last request (§2.4). |

`row_count < lines` is never truncation by itself — it means "there was no more,"
disambiguated by `has_more`. A request the server *refuses* to serve is a **400**, never
a short answer. So there is no `truncated` field, because there is no case it would
describe.

**Accepted, named limitation.** On a `saturated` pane, an absolute index held across
requests can shift by however many rows were evicted in between, and **the server cannot
detect it** — tmux exposes no monotonic eviction counter, and it is not derivable from
`history_size` (which is pinned) or from the poll cycle (which cannot know how many rows
scrolled between two samples). This is stated rather than engineered around: the
mitigation is a larger `history-limit` (§1.4), and `saturated: true` is how a caller
learns the guarantee is off. Do not add per-pane eviction tracking to chase it.

### 3.4 Server-side conversion

```python
# one atomic tmux invocation (§2.7) yields H, P, and the capture together
if before is None:
    # unchanged path: capture_pane(name, lines) -> `-S -{lines}`, -E defaults to visible bottom
    start     = max(0, H - lines)
    row_count = total - start          # = lines + P, clamped at the top of history
else:
    row_count = min(lines, before)     # `before` rows exist below it, at most
    start     = before - row_count
    # -S (start - H)   -E (start + row_count - 1 - H)

has_more  = start > 0
saturated = H >= history_limit
```

`before` pointing into the visible screen is legal and needs no special case — positive
`rel` values up to `pane_height - 1` are valid tmux coordinates.

The atomic read (§2.7) removes the race that would otherwise sit between `display-message`
and `capture-pane`. **If a future change ever splits them, read `H` *before* the capture,
never after** — an early read under-reports `start`, so the next page **overlaps**
(duplicate rows, visible and dedupable); a late read over-reports it, so the next page
**gaps** (invisible data loss). Overlap is the only acceptable error direction.

### 3.5 The poll path does not change

`main.py:1405`'s separation stands, and this design depends on it. The poll cycle's
`snapshot_all()` fans out `capture_pane(name)` at the fixed 30-line default across every
session (~38 on the reference deployment) every ~2s into one shared cache consumed
simultaneously by the PWA, muxplex-deck, and agents. A per-request depth there would
force either forking that shared contract or a live tmux call per session per poll — the
exact cost the ceiling exists to prevent. **Paging lives entirely on the on-demand
single-target path.** `snapshot_all()`, `update_session_cache()`, and
`GET /api/sessions`' `snapshot` field are untouched.

The one poll-path change is free and additive: extend `enumerate_sessions()`'s existing
format string with `#{history_size}`, `#{history_limit}`, and `#{alternate_on}` (§2.6).
Zero extra subprocesses. Its documented tolerance for short lines widens from "fewer than
3 tabs" to "fewer than 6" — the `partition` chain already handles it; update the
docstring.

### 3.6 `POST .../input` is out of scope

`/input`'s read-back answers "what did my keystrokes do," which is always at the live end.
`lines` already covers it. `before` there would be meaningless. Unchanged.

### 3.7 Federation is out of scope

No `GET /api/federation/{device_id}/sessions/{name}` proxy exists today (the federation
surface is `sessions`, `generate-key`, `connect`, `bell/clear`, and session create). Same
posture as the follow-up queue and `POST /api/focus`: **no federation proxy in v1**, added
only when a real consumer exists. `GET /api/federation/sessions` continues to spread the
local `/api/sessions` entry verbatim, so any Phase 2 depth metadata (§4) rides along for
free with no federation code, exactly as `created_at` did.

---

## 4. Phasing

| Phase | Change | Ships value alone? |
|---|---|---|
| **0** | Retire `ensure_history_retention`/`SESSION_HISTORY_LIMIT`; correct the three docs (§1) | Yes — removes a false guarantee |
| **1** | `before=` + `start`/`row_count`/`total`/`has_more`/`saturated` on `GET /api/sessions/{name}` (§3) | Yes — **this is the feature** |
| **2** | `history_size`/`history_limit`/`alternate_screen` on `GET /api/sessions` entries, from the free enumerate fields (§2.6, §2.8) | Yes — lets a UI/client decide affordances without a probe request |
| **3** | `muxplex-client`: `session(name, lines=, before=)`, new `SessionSnapshot` fields, optional backward-paging generator | Yes — sugar over the documented loop |

Phase 0 is a prerequisite for Phase 1 only in the sense that shipping paging while three
docs promise a retention guarantee that does not exist would page callers toward a wall
whose depth muxplex misreports. Phase 2 and 3 are independent.

---

## 5. Contract discipline

- **Additive only.** One new optional query param; five new response keys; no existing
  key renamed, removed, or redefined. A pre-feature client sending no `before` gets
  byte-identical responses and ignores the new keys.
- **Version tolerance both directions.** `muxplex-client`'s
  `parse_session_snapshot()` (`client/muxplex_client/_protocol.py:85`) must default every
  new field (`None`/`False`) so a new client against a pre-feature server still parses.
  New `SessionSnapshot` dataclass fields therefore need defaults.
- **Lockstep.** `muxplex-client` and server versions move together;
  `test_client_contract.py` turns red in the same PR on drift.
- **No new mirrored constants.** `MAX_CAPTURE_LINES`/`DEFAULT_CAPTURE_LINES` are
  unchanged (§0.4), so `test_client_contract.py:310`/`:314` need no companions.
- **Doc updates in the same PR:** `docs/API_SEMANTICS.md` (new subsection: the absolute
  coordinate contract, the `has_more`/`saturated` truth table, the saturation limitation);
  `docs/AGENT_GUIDE.md` §6.3 (the paging loop, and the §1 corrections); `AGENTS.md` (only
  if the poll/on-demand split needs restating — it does not change).

---

## 6. Evidence requirements

Per `AGENTS.md`: **never on a host serving a live muxplex.** `make test` (DTU); any test
that touches a real tmux server layers its own `-L <unique-name>` socket on top of
conftest's autouse `TMUX_TMPDIR` isolation, as `test_integration.py::tmux_server` already
does. No `pkill`, no bare `kill-server`.

Each item below has a measured reference value from §2, so a run either reproduces it or
the design is wrong.

1. **Drift regression (the reason this design exists).** Emit N lines, capture a page,
   emit 31 more, re-request the *same absolute range*. Assert byte-identical content, and
   assert the naive relative request would have returned different content. Reference:
   §2.2 / §2.3.
2. **Round-trip completeness.** Emit 5000 uniquely-numbered lines into a pane with
   `history-limit` ≥ 6000; page backward at `lines=500` until `has_more: false`;
   concatenate. Assert **every** line present, **no** duplicates, **no** gaps.
3. **True beginning vs. retention wall.** Same loop against `history-limit=200`. Assert
   the terminal page reports `has_more: false, saturated: true`, and that the unsaturated
   case reports `has_more: false, saturated: false`. Reference: §3.3 truth table.
4. **`before=0`.** 200, `row_count: 0`, `has_more: false`. Not a 4xx.
5. **Bounds.** `before=-1` → 400. `before=total+1` → 400. `lines=0`/`lines=2001` → 400,
   messages unchanged.
6. **Backward compatibility.** No `before`: response is byte-identical to pre-change for
   `name`/`snapshot`/`lines`/`bell`/`last_activity_at`, including `?lines=30` returning
   `30 + pane_height` rows. Reference: §2.1.
7. **Poll path untouched.** `snapshot_all()` still issues exactly one 30-line
   `capture-pane` per session per cycle. Assert the argv, not the timing.
8. **Atomicity.** Assert the single-invocation argv shape of §2.7 (`";"` as its own argv
   element) — the guard against a future refactor splitting it and reintroducing the
   race. Same style as `test_input.py:316`'s argv assertion.
9. **Cost.** Assert a deep narrow page (`lines=10` at `before≈total-40000`) returns
   ≤ 2 KB. Reference: §2.5 (500 B measured).
10. **Retention truth (Phase 0).** Assert `ensure_history_retention` is gone and that a
    session created on a host with global `history-limit=2000` reports
    `history_limit: 2000` — not 5000. Reference: §1.2.

---

## 7. Summary

- **The 2000-line ceiling is not the problem; it is a correctly-sized *window* cap being
  used as a *depth* cap.** Measured cost is O(window), so paging serves unbounded depth
  at unchanged per-request cost. `MAX_CAPTURE_LINES` stays at 2000.
- **Raw `-S`/`-E` passthrough is not enough** — measured drift under a live pane, and
  measured silent clamping below the layer muxplex can observe.
- **One integer conversion fixes both.** Absolute indices anchored on `history_size`,
  converted server-side from a value read atomically with the capture.
- **`has_more` + `saturated` fully disambiguate the last page**, which is what "no silent
  truncation" actually requires.
- **The retention guarantee three documents promise does not exist**, and on an unmanaged
  host retention equals the request cap exactly. Fix that first, or paging pages toward a
  wall muxplex misreports.
- **The PWA needs none of this** — the live terminal already reaches full tmux history via
  copy-mode.

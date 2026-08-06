# Implementation Specification — Per-Device Sync Groups

Status: **SHIPPED in v0.31.4 (2026-08-01).** Retained as an architectural
decision record. §0's blocking finding — that a single shared ttyd process
turns per-device session selection into a silent keystroke-misdirection
hazard — is the reason this feature shipped with the terminal single-owner
guard (`terminal_session`/`terminal_group`, `409 terminal_conflict`,
`?takeover=true`) rather than as sync groups alone. **That constraint is no
longer live.** The per-session-ttyd architecture shipped in **v0.35.0** — one
ttyd per session, each on its own `AF_UNIX` socket — so two devices on two
different sessions no longer share a resource, and `409 terminal_conflict` was
deleted outright (it cannot fire; `grep -c terminal_conflict muxplex/main.py`
is 0). WS 4409 survives with a narrower meaning: "you asked for a session your
sync group did not select." See `docs/plans/2026-08-02-per-session-ttyd-plan.md`
§7 for the guard decisions, AGENTS.md → "ttyd is loopback-only by design …  now
per-session, over AF_UNIX", and `docs/API_SEMANTICS.md` → "Per-session ttyd".

§0 below is retained as written, because the hazard it identifies is exactly
what motivated the single-owner guard at the time — but read it as history, not
as a description of how the terminal works today.

---

## §0. BLOCKING FINDING — the shared ttyd breaks this feature, and would break it *dangerously*

**Verdict: the fact list in the brief is accurate. The design is not.** Sync groups
work perfectly for `active_view`. Applied to `active_session` without a companion
change to the terminal path, they convert today's *honest yank* into a **silent
wrong-session terminal that accepts keystrokes**.

### The constraint, verified

| Fact | Evidence |
|---|---|
| One ttyd process, server-wide | `ttyd.py:42` — a single module-global `_active_process` |
| One hardcoded port | `ttyd.py:36` — `TTYD_PORT: int = 7682` |
| The session name is baked into that process's argv at spawn | `ttyd.py:206-217` — `ttyd -W -m 3 -p 7682 tmux attach -t <session_name>` |
| `connect` unconditionally kills and respawns the one process | `main.py:1220-1221` — `await kill_ttyd(); await spawn_ttyd(name)` |
| The WS proxy has no session parameter — it dials the one port | `main.py:2062` — `ttyd_url = f"ws://localhost:{TTYD_PORT}/ws"` |
| The browser's terminal URL carries no session either | `terminal.js:66` — `url = proto + '//' + location.host + '/terminal/ws'` |
| That terminal is **writable** | `ttyd.py:208` — `-W` |

**So: two devices cannot view two different sessions at the same time. Not "not
yet" — the session identity exists in exactly one place (one process's argv), and
every client shares it.**

### Why it is worse than "the feature doesn't help fullscreen"

Today `followRemoteActiveSession()` (`app.js:513-535`) *converges* every browser
onto the same session. Convergence is what makes the single ttyd honest: everyone
is looking at the same thing because everyone agreed to. Sync groups deliberately
remove that convergence — and nothing else was holding the invariant up.

Trace it with device A private on session X, device B global opening session Y:

1. B `POST /api/sessions/Y/connect` → `kill_ttyd()` + `spawn_ttyd(Y)`.
2. A's relay dies when ttyd dies. A's browser fires `close` (`terminal.js:149`),
   schedules a reconnect ~1s later.
3. `_reconnectAttempts` is 1, so `connect()` (`terminal.js:166`) takes the direct
   path — no `/connect` POST — and dials `/terminal/ws`.
4. ttyd **is** listening (it's B's, attached to Y). The relay succeeds.
5. **A's UI still says X. A's terminal is now session Y. A's keystrokes go to Y.**
   `_reconnectAttempts` resets to 0 on the first data frame (`terminal.js:133`),
   so nothing ever escalates and nothing ever corrects it.

That is a **keystroke-misdirection hazard**, not a rendering bug. A helper who
believes they are in their own scratch session is typing into the owner's live
agent pane. Given this repo's own history around irrecoverable tmux sessions,
that is not a defect class worth shipping to find out about.

There is a second, rarer mode: on a quiet session no data frame arrives, so
`_reconnectAttempts` reaches 2 and `terminal.js:174-192` POSTs
`/api/sessions/{_currentSession}/connect` — seizing the terminal back. Both
devices then flap indefinitely, each PTY churn destroying both scrollbacks.

### What I need you to decide

The spec below is written for **Option 1**, which I recommend.

**Option 1 — Ship groups + an explicit single-owner terminal (recommended).**
`active_view` becomes genuinely per-device (the whole point, and the majority of
the pain). `active_session` becomes per-device *state*. The terminal stays a
declared, single-claim resource: a new `terminal_session` / `terminal_group` pair
makes ttyd's actual attachment a first-class fact, `connect` returns **409** when
another group holds it, and `/terminal/ws` **refuses** rather than relaying the
wrong session. ~30 lines of server code on top of the group plumbing. Nothing is
silent. Default behavior is unchanged because `terminal_group` can only leave
`"global"` by an explicit human opt-out.

**Option 2 — View-only v1.** Per-group `active_view` only; `active_session` stays
global. Smaller, but the helper still yanks the owner into fullscreen, and per
`AGENTS.md` ("never as frontend-only state or logic") you can't fix that in the
client. Weaker feature, and you'd revisit the same schema in a month.

**Option 3 — Fix the root cause first: one ttyd per session.** Port registry,
dynamic allocation, idle reaping, orphan sweeping across a range, federation WS
passthrough. This is the real answer and it makes the 409 in Option 1 stop firing
forever. It is also a separate project that touches `cgroup_escape` and the
`kill_orphan_ttyd()` port sweep that `AGENTS.md:183-199` already flags as a
second-instance hazard. **Do not bundle it with this change.**

Option 1's wire contract is forward-compatible with Option 3: when per-session
ttyd lands, `terminal_group` stops being contended and the 409 becomes
unreachable. No client rewrite.

Everything below assumes Option 1.

---

## §1. Overview

Add a **sync group** to each device. A group owns its own
`active_session` / `active_remote_id` / `active_view`. Group `"global"` is stored
in — and *is* — today's top-level `state.json` keys, so every existing client is
untouched. A browser can flip itself between `"global"` and its own private group
`"device:<device_id>"`.

Six endpoints gain an optional `device_id` query parameter that selects which
group is read or written. Omitting it means `"global"`, exactly as today.

### Non-goals

- Cross-device group membership (pairing a deck to a specific browser). The
  string-valued group id exists so this needs no migration later, but v1
  validation rejects it (§4.6).
- Per-session ttyd (§0 Option 3).
- Federation: remote sessions and `/federation/{id}/terminal/ws` are the remote
  instance's business. Unchanged.
- Making `deck.js` a registered device (§9).

---

## §2. `state.json` schema

### 2.1 Shape

```jsonc
{
  // ── group "global" — these keys ARE the global group's storage ──
  "active_session": null,          // unchanged
  "active_remote_id": null,        // unchanged
  "active_view": "all",            // unchanged

  "session_order": [],             // unchanged, NOT group-scoped
  "sessions": {},                  // unchanged, NOT group-scoped

  "devices": {
    "d-abc12345": {
      "label": "...",              // unchanged
      "viewing_session": null,     // unchanged
      "view_mode": "grid",         // unchanged
      "last_interaction_at": 0.0,  // unchanged
      "last_heartbeat_at": 0.0,    // unchanged
      "sync_group": "global"       // NEW
    }
  },

  // ── NEW: non-global groups only ──
  "sync_groups": {
    "device:d-abc12345": {
      "active_session": null,
      "active_remote_id": null,
      "active_view": "all"
    }
  },

  // ── NEW: the one ttyd, made explicit ──
  "terminal_session": null,        // str | None — what ttyd is attached to
  "terminal_group": "global"       // str — which group claimed it
}
```

### 2.2 The mirroring rule: **there is no mirroring**

**`sync_groups` NEVER contains the key `"global"`. The top-level
`active_session` / `active_remote_id` / `active_view` keys are group `"global"`'s
one and only storage.**

This is the single most important schema decision. The alternative — a
`sync_groups["global"]` entry mirrored into the top-level keys — creates two
copies of one truth, and therefore a divergence bug, a reconciliation routine,
and a "which one wins" question. With one copy, all three disappear. There is no
source-of-truth question because there is only one source.

Reading and writing dispatch on the group id (§3.2): `"global"` → top-level keys;
anything else → `sync_groups[group]`. That two-line branch is the entire
mechanism.

**Invariant, enforced loudly:** `"global" in state["sync_groups"]` is a bug.
`normalize_state()` raises `ValueError` on it. Do not repair it silently.

### 2.3 Group ids

- `"global"` — the shared group. Default for every device.
- `"device:<device_id>"` — a device's private group. Deterministic from the
  device id, so no allocation, no collision handling, and no bookkeeping beyond
  the device record that already exists.

Group ids are opaque strings on the wire. The server mints only the two forms
above.

### 2.4 Schema upgrade of an existing `state.json`

All normalization happens in **exactly one function**, called by `load_state()`.
No read site anywhere else may use `.get(..., default)` to paper over an absent
key — that is how a schema question turns into a scattered silent fallback.

`normalize_state()` fills absent keys only:

| Absent key | Filled with | Why this value |
|---|---|---|
| `sync_groups` | `{}` | No non-global groups existed. |
| `devices[*].sync_group` | `"global"` | Every pre-upgrade device was global. |
| `terminal_group` | `"global"` | Only global existed to claim it. |
| `terminal_session` | **the current `active_session` value** | Before groups, ttyd was *always* attached to `active_session` (`main.py:1220-1226`, and `_prepare_ttyd_for_reconnect()` at `main.py:1975` reads it for exactly this reason). This is a correct restatement of the old invariant, not a guess. |

No migration write is needed. Keys materialize on the next `save_state()`.

**Downgrade:** an older muxplex reading a new `state.json` ignores `sync_groups`,
`terminal_*`, and `sync_group`, and reads the top-level keys — i.e. it behaves as
the global group. Correct by construction.

---

## §3. `muxplex/state.py` — new module surface

```python
GLOBAL_GROUP: str = "global"
GROUP_FIELDS: tuple[str, ...] = ("active_session", "active_remote_id", "active_view")
```

### 3.1 Factories

```python
def empty_group() -> dict:
    """Return a fresh group state dict (all three GROUP_FIELDS at defaults)."""
    # {"active_session": None, "active_remote_id": None, "active_view": "all"}
```

- `empty_state()` gains `"sync_groups": {}`, `"terminal_session": None`,
  `"terminal_group": GLOBAL_GROUP`.
- `empty_device()` gains `"sync_group": GLOBAL_GROUP`.

### 3.2 Group resolution and access

```python
def device_group_id(device_id: str) -> str:
    """Canonical private-group id for *device_id*: f"device:{device_id}"."""

def resolve_group(state: dict, device_id: str | None) -> str:
    """Return the group id *device_id* belongs to.

    None -> GLOBAL_GROUP.
    A device_id not present in state["devices"] -> raises KeyError.
    Never falls back to GLOBAL_GROUP for an unknown device: silently routing an
    unrecognised device's write to the shared group is precisely the yank this
    feature exists to prevent.
    """

def read_group_state(state: dict, group: str) -> dict:
    """Return a COPY of *group*'s three GROUP_FIELDS.

    GLOBAL_GROUP reads the top-level keys; any other group reads
    state["sync_groups"][group]. Unknown non-global group -> KeyError.
    """

def write_group_state(state: dict, group: str, updates: dict[str, object]) -> None:
    """Apply *updates* (a subset of GROUP_FIELDS) to *group*'s slot, in place.

    GLOBAL_GROUP writes the top-level keys; any other group writes
    state["sync_groups"][group].
    Unknown non-global group -> KeyError.
    A key outside GROUP_FIELDS -> ValueError. (Fail loudly; a typo'd field name
    must not become a silently-ignored write.)
    """

def ensure_group(state: dict, group: str) -> bool:
    """Create *group* in state["sync_groups"] if absent, SEEDED FROM GLOBAL.

    Returns True if it was created.
    No-op returning False for GLOBAL_GROUP.

    Seeding (not defaulting) is deliberate: "go independent" means "detach,
    keeping what I'm currently looking at", not "teleport me to the All view".
    It is the exact mirror of rejoin-adopts-global (§7.4).
    """
```

### 3.3 Lifecycle

```python
def gc_sync_groups(state: dict) -> list[str]:
    """Delete every sync_groups key no live device claims. Returns removed ids.

    Target set == {d["sync_group"] for d in state["devices"].values()} - {GLOBAL_GROUP}.

    This is the whole leak-prevention story: groups are defined by their
    membership, so a group with no members is garbage by definition, from any
    cause (prune, toggle-back-to-global, device_id regenerated after a
    localStorage wipe). No new TTL, no new timer -- it rides the existing
    prune_devices() call site in the poll cycle.
    """

def clear_missing_active_sessions(state: dict, live: set[str]) -> list[str]:
    """For EVERY group (global + all sync_groups), null active_session when it
    is not in *live*. Returns the group ids that were cleared.

    Replaces the global-only check at main.py:366-368.
    active_remote_id is deliberately NOT touched -- matching today's behavior.
    """

def normalize_state(state: dict) -> dict:
    """Fill absent schema keys per the §2.4 table. Raise ValueError if
    GLOBAL_GROUP is present in state["sync_groups"]."""
```

### 3.4 Changed existing functions

```python
def register_device(
    state: dict,
    device_id: str,
    label: str,
    viewing_session: str | None,
    view_mode: str,
    last_interaction_at: float,
    sync_group: str | None = None,   # NEW, keyword-safe, appended last
) -> None:
```

Semantics of `sync_group`:

- `None` → **leave the device's current `sync_group` unchanged**; a brand-new
  device gets `GLOBAL_GROUP`. (Version tolerance per `AGENTS.md`: "the server
  should tolerate their absence". A client that doesn't know about groups must
  not be able to reset a group it doesn't know exists.)
- A string → set it, and call `ensure_group()` so the group exists.

`register_device` does **not** validate the group id — validation is a boundary
concern and lives at the endpoint (§4.6).

`load_state()` returns `normalize_state(...)` of both the parsed file and
`empty_state()`. `prune_devices()` is unchanged.

---

## §4. Endpoint contracts

### 4.0 Query param vs body field — the rule and its reason

**Routing selector → query param. Device attribute → body field.**

`device_id` on the five state endpoints answers *"which group's state am I
addressing?"*. That is a selector, not data being written. Three of the five
(`POST /connect`, `DELETE /current`, `GET /state`) have no request body at all,
so a query param is the only uniform option; forcing bodies onto them to carry a
selector would be a bigger contract change than the feature. On `PATCH
/api/state` a body field would additionally *look* like a field being patched —
`StatePatch` means "the values to write", and `device_id` is not one of them.

`sync_group` on `POST /api/heartbeat` goes in the **body**, because there it is
genuinely a field of the device record being written — the same category as
`label` and `view_mode`.

**Unknown `device_id` → 404** on every endpoint that accepts it:

```json
{"detail": "Unknown device_id 'd-xxxxxxxx'; send POST /api/heartbeat first"}
```

Not a fallback to global. A device that has aged out of the 300s registry
(`state.py:143-156`) must not silently start driving everyone's screen. Client
recovery is specified in §7.6 and self-heals within one heartbeat interval.

### 4.1 `GET /api/state`

```
GET /api/state[?device_id=<id>]
```

- **No `device_id`** — byte-identical to today plus the new persisted keys that
  are in `state.json` anyway (`sync_groups`, `terminal_session`,
  `terminal_group`) and a `"sync_group": "global"` echo. Every existing key keeps
  its exact current value. (Additive response keys are the established pattern
  here — see `settings_updated_at`, `docs/API_SEMANTICS.md`.)
- **With `device_id`** — resolve the group, then **overwrite** the response's
  `active_session` / `active_remote_id` / `active_view` with that group's
  values, and set `"sync_group": <group>`.

Overwriting (rather than nesting the resolved values under a new key) is what
lets `restoreState()`, `followRemoteActiveView()`, and `muxplex_client`'s
`parse_server_state` keep working with a URL change and nothing else. Raw
per-group data is still fully visible in `sync_groups` for anyone who wants it.

Response gains, always present:

| Key | Type | Meaning |
|---|---|---|
| `sync_group` | `str` | Which group resolved this response |
| `terminal_session` | `str \| None` | Session the single ttyd is attached to |
| `terminal_group` | `str` | Group that claimed the terminal |
| `sync_groups` | `dict` | Non-global groups (from `state.json`) |

`settings_updated_at` merging is unchanged.

### 4.2 `PATCH /api/state`

```
PATCH /api/state[?device_id=<id>]
Body: StatePatch (UNCHANGED — no new fields)
```

- `session_order` is **not** group-scoped: it stays a top-level write regardless
  of `device_id`. It describes the sessions, not a view of them.
- `active_session` / `active_remote_id` / `active_view` route to the resolved
  group via `write_group_state()`, honoring `patch.model_fields_set` exactly as
  today.
- Returns the same projected shape as §4.1 for the same group.

`StatePatch` is unchanged — do not add `device_id` to it.

### 4.3 `POST /api/sessions/{name}/connect`

```
POST /api/sessions/{name}/connect[?device_id=<id>][&takeover=true]
```

Order of operations (existing guards first, unchanged):

1. `_require_valid_session_name(name)` → 400.
2. `name in get_session_list()` → else 404.
3. Resolve group from `device_id` → 404 if unknown.
4. **NEW — terminal-claim gate.** Under `state_lock`, read `terminal_session` /
   `terminal_group`:

   ```
   if terminal_session is not None
      and terminal_group != caller_group
      and not takeover:
          -> 409
   ```

   409 body:
   ```json
   {
     "terminal_conflict": true,
     "detail": "The terminal is attached to 'sessX' for another device.",
     "terminal_session": "sessX",
     "terminal_group": "device:d-abc12345"
   }
   ```
   `terminal_conflict: true` is the discriminator, following the established
   `{"backstop": true, ...}` precedent (`docs/API_SEMANTICS.md`) for telling one
   409 cause from another. **No write and no ttyd action happens on 409.**

   **This gate cannot fire for any of today's clients.** They send no
   `device_id`, so `caller_group == "global"`; `terminal_group` is `"global"`
   until a device explicitly opts out and connects. Both global ⇒ equal ⇒ no
   409.

5. Same-session short-circuit (`main.py:1211-1217`), now keyed on
   `terminal_session` rather than `active_session`, and it must **still write**
   the caller's group `active_session` (a private device connecting to the
   session the terminal already shows must record its own selection):

   ```
   if name == terminal_session and _ttyd_is_listening():
       write_group_state(state, group, {"active_session": name})
       state["terminal_group"] = group      # co-viewing: last claimant named
       return {...}
   ```
6. `kill_ttyd()`, `spawn_ttyd(name)` — unchanged.
7. Under `state_lock`: `write_group_state(state, group, {"active_session": name})`;
   `state["terminal_session"] = name`; `state["terminal_group"] = group`.

Response (additive):
```json
{"active_session": "sessX", "ttyd_port": 7682,
 "sync_group": "global", "terminal_session": "sessX"}
```

### 4.4 `DELETE /api/sessions/current`

```
DELETE /api/sessions/current[?device_id=<id>]
```

1. Resolve group → 404 if unknown.
2. **`kill_ttyd()` iff `terminal_group == caller_group`.** Otherwise the caller
   does not hold the terminal and must not kill it — closing your own private
   fullscreen must not black out someone else's terminal.
3. Always clear the caller's group `active_session` to `None`.
4. When the terminal was released: `terminal_session = None`,
   `terminal_group = GLOBAL_GROUP`.

Response: `{"active_session": null, "sync_group": "<group>", "terminal_released": true|false}`

Existing clients (`terminal_group == "global"` == their group) get exactly
today's behavior, including the case of two global browsers on one session where
one closing drops the other's relay — unchanged, and self-healing as it is today.

### 4.5 `GET /api/view`

```
GET /api/view[?sort=attention][&device_id=<id>]
```

Resolve `active_view` and `active_session` (`main.py:1074-1075`) from the group
instead of the top level. Everything else — `filter_visible`, `needs_attention`,
`_attention_order`, the `views` list, `sessionKey`, the 400 on an unknown `sort`
— is untouched. Response gains `"sync_group": <group>`.

### 4.6 `POST /api/heartbeat`

```python
class HeartbeatPayload(BaseModel):
    device_id: str
    label: str
    viewing_session: str | None
    view_mode: Literal["grid", "fullscreen"]
    last_interaction_at: float
    sync_group: str | None = None    # NEW, optional
```

**Validation (v1), in the endpoint, before `register_device`:**

```
sync_group is None                              -> OK (leave unchanged)
sync_group == "global"                          -> OK
sync_group == f"device:{payload.device_id}"     -> OK
anything else                                   -> 400
```

400 body: `{"detail": "sync_group must be 'global' or 'device:<own device_id>'"}`

Rejecting `device:<someone-else>` today is the tight-then-widen choice: allowing
it would ship untested surface with no consumer, and relaxing the check later
(when a deck is paired to a browser — the exact case that motivated a string over
a boolean) is purely additive and needs **no schema change**, which is the whole
point of the group model.

Response gains `"sync_group": <the device's resolved group>`.

Group creation happens here and only here, via `ensure_group()` — one site, seeded
from global (§3.2).

### 4.7 `WS /terminal/ws` — the loud backstop

```
/terminal/ws[?device_id=<id>]
```

Pre-`accept()`, alongside the existing `_ws_auth_check` (`main.py:2011`, same
close-before-accept pattern):

- **No `device_id`** → today's path exactly. No new behavior.
- **Unknown `device_id`** → `close(code=4404)`.
- **Otherwise** → resolve the group; let `want = read_group_state(...)["active_session"]`:
  ```
  if want is None or want != state["terminal_session"]:
      close(code=4409); return
  ```

This is the single most important line in the spec. It is the server-side
resolution of the §0 hazard, and it holds **whether or not any client behaves
correctly** — which is exactly the discipline `AGENTS.md:33-39` prescribes
("resolve it server-side rather than shipping more logic for each of PWA /
sidecar / agents to port"). A device can no longer be shown, or type into, a
session it did not select.

There is no race with the normal path: `openSession()` (`app.js:3379-3387`)
`await`s `/connect` — which sets `terminal_session` — before mounting the
terminal, so they are equal by the time the WS opens.

`_prepare_ttyd_for_reconnect()` (`main.py:1967-1987`): change its one read from
`state.get("active_session")` to `state["terminal_session"]`. Behaviorally
identical for the global-only case (they are equal), and correct once groups
exist — it respawns what the terminal *was*, not what some group *wants*.

`/federation/{remoteId}/terminal/ws` (`main.py:2157+`): **unchanged.**

### 4.8 Residual gap (state it in the PR, do not paper over it)

A terminal client that supplies no `device_id` gets no §4.7 protection. After
this change `app.js` always supplies one (§7.2), and it is the only terminal
client in the tree — `deck.js` opens no terminal, `muxplex_client` has no WS
surface. The gap is real but currently unpopulated. Closing it would mean
*requiring* `device_id` on `/terminal/ws`, which is a breaking change and is
therefore out of scope.

---

## §5. Poll cycle (`main.py` `_run_poll_cycle`)

### 5.1 Step 7 — write site 4, session-vanished cleanup

Replace `main.py:366-368`:

```python
# was:
if state["active_session"] not in name_set:
    state["active_session"] = None

# becomes:
cleared = clear_missing_active_sessions(state, name_set)
if cleared:
    _log.info("poll: cleared vanished active_session for group(s) %s", cleared)
```

Every group, not just global. A private group whose session was killed from
another machine must not keep pointing at a corpse — that would strand its
`/terminal/ws` on a permanent 4409 with no way for the user to understand why.

Also in step 7: if `state["terminal_session"] not in name_set`, set it to `None`
and `terminal_group` to `GLOBAL_GROUP`. Do **not** call `kill_ttyd()` here —
today's code does not kill ttyd on a vanished session and this change must not
start.

### 5.2 Step 11 — device pruning

```python
removed_devices = prune_devices(state)
removed_groups = gc_sync_groups(state)          # NEW
if state["terminal_group"] in removed_groups:   # NEW — release the terminal
    await kill_ttyd()
    state["terminal_session"] = None
    state["terminal_group"] = GLOBAL_GROUP
    _log.info("poll: released terminal held by pruned group")
```

`gc_sync_groups()` must run **after** `prune_devices()` — it derives its target
set from the surviving devices.

The terminal release is what stops a closed laptop from holding the terminal
hostage: within 300s of the private device going silent, its group is collected,
ttyd is freed, and every other client's `connect` stops 409ing. Without it, the
409 gate would have a deadlock with no recovery short of restarting the service.

`apply_bell_clear_rule()` and the federation bell-clear block (steps 9-10) read
`devices[*].viewing_session` / `view_mode`, not `active_session` — **unchanged,
no group awareness needed.**

---

## §6. Files to change — server

| File | Change |
|---|---|
| `muxplex/state.py` | `GLOBAL_GROUP`, `GROUP_FIELDS`, `empty_group()`, `device_group_id()`, `resolve_group()`, `read_group_state()`, `write_group_state()`, `ensure_group()`, `gc_sync_groups()`, `clear_missing_active_sessions()`, `normalize_state()`; `empty_state()` + `empty_device()` + `register_device()` extended; `load_state()` normalizes. Update the module docstring's schema block — it is the schema's documentation of record. |
| `muxplex/main.py` | New `_resolve_group_or_404(state, device_id) -> str` helper; `device_id` param on `get_state`, `patch_state`, `connect_session`, `delete_current_session`, `get_view`, `terminal_ws_proxy`; `takeover` param on `connect_session`; `sync_group` on `HeartbeatPayload` + validation in `heartbeat`; terminal-claim gate; poll-cycle steps 7 and 11; `_prepare_ttyd_for_reconnect()` reads `terminal_session`. |
| `muxplex/ttyd.py` | **No change.** |
| `docs/API_SEMANTICS.md` | New section: sync groups, the no-mirroring rule, `terminal_session`/`terminal_group`, the 409 `terminal_conflict` discriminator, WS close code 4409, and the §0 constraint stated plainly for client authors. The existing bullet "`active_view` / `active_session` are server-global" must be amended, not left to contradict the new behavior. |
| `docs/AGENT_GUIDE.md` | §4's "leave both alone" guidance gains the `device_id` opt-out and the 409. |

---

## §7. Frontend spec — `muxplex/frontend/app.js` + `index.html`

### 7.1 Module state

```js
const SYNC_GROUP_STORAGE_KEY = 'muxplex-sync-group';
let _syncGroup = 'global';   // 'global' | 'device'  -- the MODE, not the id
```

Store the **mode**, not the full group id. If `_deviceId` is ever regenerated
(localStorage wipe), a stored `"device:d-oldid"` would be stranded and 400 on
every heartbeat; a stored `"device"` re-derives correctly.

```js
function syncGroupId() {
  return _syncGroup === 'device' ? 'device:' + _deviceId : 'global';
}

function initSyncGroup() {
  // Same try/catch shape as initDeviceId() (app.js:392-406): localStorage may
  // be blocked. Blocked -> stay 'global' for the session, no persistence.
}
```

### 7.2 Sending `device_id` — always, not conditionally

```js
function withDevice(path) {
  return path + (path.indexOf('?') === -1 ? '?' : '&')
       + 'device_id=' + encodeURIComponent(_deviceId);
}
```

Apply to **every** call in `app.js` that touches group state, unconditionally —
including when the mode is `'global'`:

| Site | Now |
|---|---|
| `restoreState()` `app.js:411` | `api('GET', withDevice('/api/state'))` |
| `pollActiveState()` `app.js:642` | `api('GET', withDevice('/api/state'))` |
| `openSession()` connect `app.js:3383` | `withDevice('/api/sessions/' + enc(name) + '/connect')` |
| `openSession()` patch `app.js:3395` | `api('PATCH', withDevice('/api/state'), {...})` |
| `closeSession()` delete `app.js:3427` | `withDevice('/api/sessions/current')` |
| `closeSession()` patch `app.js:3427` | `api('PATCH', withDevice('/api/state'), {...})` |
| `switchView()` `app.js:1962`, `app.js:1883`, `app.js:2849` | `api('PATCH', withDevice('/api/state'), { active_view })` |
| `terminal.js` WS URL | `'/terminal/ws?device_id=' + encodeURIComponent(deviceId)` |
| `terminal.js` escalation POST `terminal.js:181` | `withDevice(...)` equivalent |

One code path, not two. A `'global'`-mode device resolves to the global group,
so semantics are identical to today — and it means the §4.7 WS guard protects
the **default** user, which is the whole point of having it.

**Ordering requirement:** `init()` must `await sendHeartbeat()` **before**
`restoreState()`. Otherwise the very first `GET /api/state?device_id=` 404s
because the device is not yet registered. One-line reorder, deterministic.

### 7.3 Heartbeat

```js
function buildHeartbeatPayload(device_id, viewing_session, view_mode,
                               last_interaction_at, sync_group) { ... }
```

Appends `sync_group` to the returned object. `sendHeartbeat()` (`app.js:3128`)
passes `syncGroupId()`.

⚠️ **`frontend/tests/test_app.mjs:226-246` calls `buildHeartbeatPayload` with 4
args in three tests.** They must be updated in the same PR (both the call sites
and a new assertion on the `sync_group` field). Also re-check
`test_frontend_js.py` — `AGENTS.md:431-453` documents it as a source-text
tripwire; if an assertion there matches a literal `'/api/state'` string, the
`withDevice()` refactor will trip it. Follow the documented rule: if behavior
didn't change, fix the assertion to assert the delegation *and* the delegate —
do not loosen it.

### 7.4 The toggle

```js
async function setSyncGroup(mode) {   // 'global' | 'device'
  _syncGroup = mode;
  try { localStorage.setItem(SYNC_GROUP_STORAGE_KEY, mode); } catch (_) {}
  renderSyncGroupControls();
  await sendHeartbeat();   // assert the new group immediately, don't wait 5s
  await pollActiveState(); // adopt the new group's selection on the next read
}
```

**Rejoining `"global"` adopts, it does not push.** This falls out of the design
with zero extra code: after the heartbeat lands, the next
`GET /api/state?device_id=` returns global's values, and the existing
`followRemoteActiveView()` / `followRemoteActiveSession()` apply them. Nothing in
`setSyncGroup` writes a selection. Verify this in review — an accidental
`PATCH` here would push the private selection to everyone, which is precisely the
bug the feature exists to prevent.

**Leaving global seeds from global** — server-side, in `ensure_group()` (§3.2) —
so going independent doesn't teleport you to the "All" view.

### 7.5 UI placement

**Header** (`index.html:44-49`, inside `.header-actions`, before
`#connection-status`):

```html
<button id="sync-group-btn" class="header-btn"
        aria-pressed="false" aria-label="Independent view"
        title="Following this server's view">&#128279;</button>
```

**Expanded/fullscreen header** (`index.html:58-63`, beside
`#settings-btn-expanded`): the same control as `#sync-group-btn-expanded`. Both
call `setSyncGroup()`; `renderSyncGroupControls()` keeps them in sync — the same
pattern the three sort selects already use (`syncSortOrderControls()`).

**Settings → Devices tab** (`index.html:277`), as the **first** field in the
panel and **outside `#multi-device-fields`**:

```html
<div class="settings-field">
  <label class="settings-label" for="setting-independent-view">Independent view</label>
  <input type="checkbox" id="setting-independent-view" class="settings-checkbox" />
</div>
<span class="settings-helper">
  Keep this device's view and session selection to itself. Other devices stop
  following it, and it stops following them. The terminal is still shared —
  only one device can have a session open at a time.
</span>
```

It must be outside `#multi-device-fields` because that block is gated on
`multi_device_enabled`, which is the **federation display toggle** and has
nothing to do with this. Nesting it there would make an unrelated setting a
hidden precondition.

The helper text tells the truth about the terminal (§0). Do not soften it.

### 7.6 404 recovery

`pollActiveState()` and `restoreState()`: on `err.status === 404`, call
`sendHeartbeat()` and skip the tick. The device re-registers and the next tick
(≤1s later) succeeds. This is the recovery path for a laptop that slept past the
300s prune. No fallback to an un-scoped request — that would silently rejoin
global.

### 7.7 Terminal conflict UI

Two new signals to handle:

1. **`openSession()`'s `/connect` → 409 with `terminal_conflict: true`.**
   `api()` already throws with `err.status` and `err.body` (`app.js:369-386`), so
   the existing `catch` at `app.js:3386` fires. Replace the generic
   `showToast(...)` + `closeSession()` for this case with an honest dialog:
   > *"**sessY** is open on another device. Opening **sessX** here will move that
   > device's terminal."* — **[Take over]** / **[Cancel]**
   [Take over] retries with `&takeover=true`. Cancel returns to the grid.

2. **`terminal.js` WS `close` event with `event.code === 4409`.**
   Currently the close handler (`terminal.js:149-158`) unconditionally schedules
   a reconnect. It must branch: **on 4409, do not reconnect.** Show the
   reconnect-overlay carrying "Terminal is showing another device's session" +
   a [Take over] button. Looping here would hammer the server and never recover.

3. **`terminal.js:174-192` escalation POST currently ignores the response
   status** — it `.catch()`es and then connects the WS regardless. It must
   inspect the status and, on 409, stop and surface (2) instead of attaching.
   This is a **required** change: leaving it is the client half of the §0 hazard.

---

## §8. `muxplex_client` and muxplex-deck — migration/compat

**Confirmed: no change required.** Both stay in group `"global"` because both
omit `device_id`, and both keep exactly today's semantics.

Verified specifics:

- `sync_client.connect()` (`client/muxplex_client/sync_client.py:178-186`) sends
  no `device_id`. Its docstring warning ("active_session is server-global. This
  moves the human's browser view too") **remains accurate** — for a global-group
  caller it is still exactly true.
- `sync_client._request()` raises on any status ≥ 400
  (`sync_client.py:98-104`), and `_protocol.map_status_error()`
  (`_protocol.py:163-190`) maps 409 → `ApiError(409, detail)`. So the new 409
  surfaces as a loud, typed error rather than a silent no-op. **That is correct
  behavior and needs no code change.**
- `_protocol.parse_server_state()` (`_protocol.py:109-110`) reads keys via
  `raw.get(...)`; the additive response keys are ignored harmlessly.
- muxplex-deck registers **no** device — grep for a heartbeat call across the
  repo finds only its own unrelated `_FailureEpisode(heartbeat_seconds=...)`
  logging. It is an anonymous federation-key holder and stays one.

**Optional follow-ups (not required, do not block this PR):**

- Add `TerminalConflict(ApiError)` to `muxplex_client` and map
  `409 + terminal_conflict: true` to it, so a deck can show "terminal in use"
  instead of a generic API error. Purely a diagnostics nicety.
- Add `device_id=` / `takeover=` passthrough to `connect()` / `set_active_view()`
  so a headless agent could hold its own group. No consumer today.

---

## §9. `deck.js` — recommendation: **follow-up, not this change**

The soft deck should **not** get a `device_id` + heartbeat in this PR.

1. **Zero user-visible gain in v1.** A registered deck would default to
   `sync_group: "global"` — byte-identical to sending nothing at all. All the
   plumbing, none of the behavior.
2. **It would be a second, parallel implementation.** `deck.js` is vanilla JS
   sharing no code with `app.js` (verified: zero `active_session` refs, no
   `device_id`, no heartbeat; it writes global via `PATCH /api/state`
   `deck.js:2354` and `POST .../connect` `deck.js:2873`). Duplicating device-id
   generation, storage, the heartbeat loop, and the toggle is the exact
   client-drift pattern `AGENTS.md:33-39` warns against, paid twice for nothing.
3. **The right moment to do it is the moment it earns its keep** — pairing a deck
   to a *specific* browser's group. That is the case the string-valued group id
   exists to make migration-free, and it arrives with the §4.6 validation
   relaxation, a real UI for choosing a target device, and tests that mean
   something.
4. **Deferring costs nothing.** `deck.js` sends no `device_id`, so it stays
   global — which is the correct default for a shared control surface.

Do add one comment above `deck.js:2354` noting that this endpoint now accepts
`?device_id=` and that the deck deliberately omits it (global by design), so the
next reader doesn't think it was overlooked.

---

## §10. Test plan

### 10.0 Environment discipline — non-negotiable

`AGENTS.md:331-383`: **never** run the suite on a host serving a live muxplex.
Use `make test` (DTU). Commit locally first so `git archive HEAD` tests the
artifact you would push. Any scratch instance: scratch `HOME`, monkeypatch
`muxplex.ttyd.TTYD_PORT` **before** importing `muxplex.main`, `tmux -L
<unique>` socket, kill only PIDs your harness captured, and verify
`GET :8088/api/instance-info` → 200 as the final step.

### 10.1 `muxplex/tests/test_sync_groups.py` (new) — state.py unit

| # | Assertion |
|---|---|
| 1 | `empty_state()` has `sync_groups == {}`, `terminal_session is None`, `terminal_group == "global"` |
| 2 | `empty_device()` has `sync_group == "global"` |
| 3 | `normalize_state({})`-style legacy dict fills all four §2.4 keys; `terminal_session` == the legacy `active_session` |
| 4 | `normalize_state` **raises `ValueError`** when `"global" in sync_groups` |
| 5 | `read_group_state(s, "global")` returns the top-level values; mutating the result does not mutate state (it's a copy) |
| 6 | `write_group_state(s, "global", {...})` writes the **top-level** keys and creates **no** `sync_groups["global"]` |
| 7 | `write_group_state` with a key outside `GROUP_FIELDS` raises `ValueError` |
| 8 | `read_group_state`/`write_group_state` on an unknown non-global group raise `KeyError` |
| 9 | `resolve_group(s, None) == "global"`; unknown `device_id` raises `KeyError` |
| 10 | `ensure_group` **seeds from global's current values**, not defaults |
| 11 | `gc_sync_groups` removes exactly the unclaimed groups; a group whose device is still registered survives |
| 12 | `clear_missing_active_sessions` nulls the vanished session in **every** group and leaves `active_remote_id` alone |
| 13 | `register_device(sync_group=None)` leaves an existing group unchanged; new device → `"global"` |

### 10.2 `muxplex/tests/test_api.py` / `test_main.py` — endpoint contract

| # | Assertion |
|---|---|
| 14 | **Regression armor.** For each of the six endpoints, a request with **no** `device_id` produces a response whose pre-existing keys are value-identical to a `main` baseline. This is the "byte-identical default" gate. |
| 15 | Unknown `device_id` → **404** on `GET /api/state`, `PATCH /api/state`, `GET /api/view`, `POST .../connect`, `DELETE .../current` |
| 16 | `PATCH /api/state?device_id=X {active_view}` for a private X leaves top-level `active_view` **untouched** and writes `sync_groups["device:X"]` |
| 17 | `PATCH /api/state?device_id=X {session_order}` writes **top-level** `session_order` even for a private X |
| 18 | `GET /api/state?device_id=X` projects X's group values over `active_*` and echoes `sync_group` |
| 19 | `GET /api/view?device_id=X` filters by X's `active_view` and marks `active` from X's `active_session` |
| 20 | Heartbeat `sync_group: "device:<other-id>"` → **400**; `"device:<own>"` → 200 and creates the group seeded from global |
| 21 | Heartbeat omitting `sync_group` does not reset an already-private device |
| 22 | Global connect while a private group holds the terminal → **409** with `terminal_conflict: true` and **no** state write and **no** ttyd call (assert via mock) |
| 23 | Same request `+ takeover=true` → 200 and `terminal_group` becomes the caller's |
| 24 | `DELETE .../current?device_id=X` when `terminal_group != X` → does **not** call `kill_ttyd`, still clears X's `active_session`, returns `terminal_released: false` |
| 25 | Poll cycle with a vanished session clears `active_session` in **global and every** `sync_groups` entry, and nulls `terminal_session` |
| 26 | Poll cycle: pruning the last device of a group removes the group **and**, if it held the terminal, kills ttyd and resets `terminal_*` |

### 10.3 `muxplex/tests/test_ws_proxy.py` — the §0 guard

| # | Assertion |
|---|---|
| 27 | `/terminal/ws` with **no** `device_id` behaves exactly as today (existing tests must pass unmodified) |
| 28 | `/terminal/ws?device_id=X` where X's `active_session != terminal_session` → closes **4409**, never accepts, never opens an upstream connection |
| 29 | `/terminal/ws?device_id=X` where they match → normal relay |
| 30 | `/terminal/ws?device_id=<unknown>` → closes **4404** |
| 31 | `_prepare_ttyd_for_reconnect()` spawns for `terminal_session`, not for any group's `active_session` |

### 10.4 `muxplex/frontend/tests/test_app.mjs` / `test_terminal.mjs`

Run with the glob — `node --test frontend/tests/*.mjs` (`AGENTS.md:386-389`).

| # | Assertion |
|---|---|
| 32 | `buildHeartbeatPayload(...5 args)` includes `sync_group`; the three existing 4-arg tests updated |
| 33 | `syncGroupId()` returns `'global'` / `'device:'+id` per mode |
| 34 | `withDevice()` appends correctly to a path with and without an existing `?` |
| 35 | `setSyncGroup('global')` issues **no** `PATCH /api/state` (adopt, never push) — assert on the fetch mock's call list |
| 36 | `setSyncGroup(...)` sends a heartbeat immediately rather than waiting for the 5s tick |
| 37 | `initSyncGroup()` with `localStorage` throwing → stays `'global'`, does not throw |
| 38 | `terminal.js` close handler with `event.code === 4409` schedules **no** reconnect |
| 39 | `terminal.js` escalation POST returning 409 does **not** proceed to open the WS |

### 10.5 End-to-end: proving two independent clients

Unit tests cannot prove this. Two artifacts, both required.

**A. Scripted two-client proof** — `scripts/proof_sync_groups.py`, run inside the
DTU or an isolated scratch instance, two independent `httpx.Client`s against one
real server:

```
1. B heartbeats (no sync_group)              -> B is global
2. A heartbeats sync_group=device:A          -> group created, seeded from global
3. B: PATCH /api/state?device_id=B {active_view:"work"}
4. A: PATCH /api/state?device_id=A {active_view:"hidden"}
5. ASSERT GET /api/state?device_id=B -> "work"      # A did not move B
6. ASSERT GET /api/state?device_id=A -> "hidden"
7. ASSERT GET /api/state              -> "work"      # global untouched by A
8. ASSERT GET /api/view?device_id=A   filtered by "hidden"
9. ASSERT GET /api/view?device_id=B   filtered by "work"
10. A: heartbeat sync_group=global
11. ASSERT GET /api/state?device_id=A -> "work"      # ADOPTED, did not push
12. ASSERT GET /api/state              -> "work"      # still B's choice
13. Stop A's heartbeats; advance past the 300s prune (monkeypatch the TTL or
    fast-forward last_heartbeat_at); run a poll cycle
14. ASSERT "device:A" not in state["sync_groups"]     # no leak
```

Steps 5-7 and 11-12 are the load-bearing ones: **5-7 is "did not yank"; 11-12 is
"adopt, not push".** Those two properties are the feature.

**B. Terminal-claim proof** (`@pytest.mark.integration`, real tmux + real ttyd,
DTU only):

```
1. Create sessions X and Y
2. A (private) POST /connect X      -> 200, terminal_group == "device:A"
3. B (global)  POST /connect Y      -> 409 terminal_conflict, terminal_session still X
4. B WS /terminal/ws?device_id=B    -> closes 4409  (B is NOT shown session X)
5. A WS /terminal/ws?device_id=A    -> relays normally
6. B POST /connect Y&takeover=true  -> 200, terminal_group == "global"
7. A WS /terminal/ws?device_id=A    -> closes 4409  (A is NOT shown session Y)
8. A DELETE /current?device_id=A    -> terminal_released: false, ttyd untouched
```

**Step 4 and step 7 are the ones that matter.** They are the direct, executable
disproof of the §0 keystroke-misdirection hazard. If either relays instead of
closing, the feature is not safe to ship.

**C. Manual browser checklist** (two real browsers, two profiles, one server):

1. Both default (global): switching a view in one still moves the other. *(No
   regression.)*
2. Toggle "Independent view" in browser A. Switch A's view. **B does not move.**
   Switch B's view. **A does not move.**
3. Reload A. It comes back independent, on its own view. *(localStorage.)*
4. A opens a session fullscreen. B tries to open a different one → the takeover
   dialog appears, naming A's session. Cancel → B stays on the grid, A's terminal
   is undisturbed.
5. Confirm takeover in B → B gets the terminal; **A's terminal shows the honest
   "another device" overlay and does not silently switch sessions.**
6. Toggle A back to "Following". A adopts B's current view immediately; **B does
   not move.**
7. Close A's browser, wait >300s, run one poll cycle: A's group is gone from
   `state.json` and B's `connect` works without `takeover`.

---

## §11. Success criteria

1. Every existing test in the suite passes with **no assertion weakened**. Where
   a source-scraping assertion in `test_frontend_js.py` trips on the
   `withDevice()` refactor, it is updated to assert the delegation *and* the
   delegate (`AGENTS.md:431-453`), never loosened.
2. Test 14 (no-`device_id` regression armor) passes for all six endpoints.
3. Tests 22, 24, 28, 30 pass — the terminal is single-owner and refuses rather
   than lies.
4. E2E proof A passes, including steps 5-7 (did not yank) and 11-12 (adopt, not
   push).
5. E2E proof B steps 4 and 7 pass — **no device is ever relayed a session it did
   not select.**
6. `gc_sync_groups` proof (A step 14) passes — no group leak.
7. `docs/API_SEMANTICS.md` documents the group model, the no-mirroring rule, the
   `terminal_conflict` 409, close code 4409, and amends the existing
   "`active_view` / `active_session` are server-global" bullet.
8. `state.py`'s module docstring schema block matches the shipped schema.
9. `uv run ruff` / `pyright` clean; `node --test frontend/tests/*.mjs` green.
10. The PR description states §0's constraint and §4.8's residual gap explicitly.
    A reviewer must not have to discover the shared-ttyd limitation from the code.

---

**Update (per-session ttyd):** §0's blocking finding above — the single,
server-wide ttyd as a shared resource requiring `terminal_conflict`/4409
arbitration — is now retired. Each tmux session gets its own ttyd bound to
its own UNIX domain socket; two devices on two different sessions are no
longer a conflict at all. See `docs/plans/2026-08-02-per-session-ttyd-plan.md` for the design and
`muxplex/ttyd.py` for the implementation. This plan's record of the
original constraint and the sync-groups work it shaped is left as-is above.

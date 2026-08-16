# Design — Giving `muxplex-deck` a choice about whose view it controls

Status: **DESIGN ONLY — no code.** A discussion artifact for the repo owner.
Extends `muxplex/docs/plans/2026-08-01-per-device-sync-groups-plan.md` (the
sync-groups ADR), whose §9 explicitly deferred this and named its shape.

Ground truth read: `muxplex` @ this checkout (`state.py`, `main.py`,
`identity.py`, `settings.py`, `frontend/app.js`, `frontend/deck/deck.js`,
`client/muxplex_client/`), `muxplex-deck` @ this checkout
(`src/muxplex_deck/`), both `AGENTS.md`, the sync-groups ADR, the two
federation plan families (`2026-03-30-multi-device-federation-*`,
`2026-04-08-federation-state-propagation-*`).

> **REV 3 (this revision).** Amends REV 2 after the owner corrected my reading
> of their federation ask. What changed:
> 1. **The correction:** "register with the federation" was never a proposed
>    new mechanism. Deck registration is already a per-deployment choice via
>    `server_url` — nothing to design (§4.3). The real requirement is that the
>    picker, opened from **any** tab, must list **every deck across the whole
>    federation**, plus an explicit scope-limiter: **"Server (shared)" stays
>    scoped to the tab's own server. No cross-server server-following.**
> 2. **The consequence, and it is not just plumbing.** Tracing the read/write
>    topology precisely (§4.6, the owner's Q2) shows that **cross-server
>    *following* is not merely unbuilt — under the REV 2 model it is not
>    expressible**, in either direction, and collapses into exactly the
>    cross-server server-following the scope-limiter rules out. This splits the
>    requirement cleanly into **discovery (crosses servers, read-only)** vs.
>    **selection (stays local)** — which turns out to satisfy the owner's stated
>    *reason* in full while honoring the scope-limiter exactly (§7.4).
> 3. **Mechanism decision made** (§8.5): federated discovery goes through a new
>    read-only fan-out modeled on `/api/federation/sessions` — explicitly **not**
>    `SYNCABLE_KEYS` settings sync, for three grounded reasons.
> 4. **New risk priced in:** a deck that *moves between servers* appears twice.
>    Fixed with the repo's own existing `device_id:name` namespacing convention.
>
> **Retained unchanged from REV 2:** corrections (a)/(b)/(c), the instance-named
> label + list header, the federation session-name-collision ship-blocker
> (§4.5), and the "no `label`/`kind`/deck-config-name field today"
> prerequisites. REV 1/REV 2 content that still holds is kept; changes are
> tagged `[REV 2]` / `[REV 3]`.

---

## 1. Problem framing — and a reframe

**Stated problem.** A browser tab on machine B, viewing server A, goes
"Independent view." The Stream Deck attached to A keeps driving A's *global*
group. Deck presses and the tab silently diverge. There is no way to make the
deck follow the tab, and no way to pin the deck to something stable.

**The reframe, and it changes the design.** "Let the deck go independent" is
not a coherent goal *as the server currently models it*. Verified in the code:
a deck in its own private **server-side** group pressing a key would call
`POST /api/sessions/{name}/connect?device_id=<deck>`, which `main.py:2145`
resolves to the deck's private group, writes `active_session` *there*,
`ensure_ttyd()`s the session — and **no browser moves**. You would have built a
remote control for a television nobody is watching.

Sync-group membership answers *"whose selection do I own?"* For a browser tab,
owning a private selection is useful because **the tab is also the thing that
renders it** — it is its own audience. A deck has no audience of its own.

So the axis the deck needs is not *member vs. independent*. It is:

> **Which group does this deck drive?**

The ADR already anticipated this:

> *"The right moment to do it is the moment it earns its keep — pairing a deck
> to a specific browser's group. That is the case the string-valued group id
> exists to make migration-free."* — ADR §9.3

**[REV 2] This reframe survives the owner's dropdown proposal, and is what
makes one of its options safe.** The dropdown's "None (fully independent)"
option is coherent *only* if "None" means **client-local state, never a private
server-side group**. See §7.0(a).

**[REV 3] It is also the load-bearing fact in the cross-server question.**
"A deck is a controller, not a store" is precisely why "follow that deck" has
no coherent cross-server meaning (§4.6). The reframe from REV 1 turns out to
answer a question asked two revisions later.

**Two axes, not one.**

| Axis | Question | Where it lives | Cost |
|---|---|---|---|
| **1. Control target** | Whose `active_session`/`active_view` do my presses write, and whose do I highlight? | **Server** (group resolution) | API change |
| **2. Display pinning** | Does my rendered session list follow that target's `active_view`, or stay where I put it? | **Client-local** | Client-only |

- *"Deck presses should reach the tab I'm actually looking at"* → **Axis 1**.
- *"Pin the deck to stay on X regardless of what any tab does"* → **Axis 2**.

**[REV 2]** Under the corrected dropdown, Axis 2 *is* the "None" option — one
control covers both axes.

**[REV 3] A third axis is now explicit**, and conflating it with Axis 1 is what
made the federation question hard to see:

| Axis | Question | Where it lives | Cost |
|---|---|---|---|
| **3. Target discovery** | *Which* devices exist and can be offered in the picker? | **Crosses servers** (read-only) | New read endpoint |

Axis 1 is a **write/ownership** concern and stays per-server. Axis 3 is a
**read/inventory** concern and now crosses. REV 2 collapsed them into one
"targets are per-server" conclusion — right about Axis 1, wrong about Axis 3.

---

## 2. Ground truth (verified, not assumed)

### 2.1 Server — the group machinery already exists and is complete

| Fact | Evidence |
|---|---|
| Groups are opaque strings; server mints `"global"` and `"device:<id>"` | `state.py:121`, `state.py:209` |
| **A group's state is `("active_session", "active_remote_id", "active_view")`** — the *which server* dimension is **already inside a group** | `state.py:122` `GROUP_FIELDS` |
| `resolve_group(state, device_id)` → the device's stored `sync_group`; `KeyError` on unknown device, **never** a fallback to global | `state.py:214-226` |
| Resolution is **one hop, not transitive** — a group id is a string, never a pointer that gets chased | `state.py:225-226` |
| Six endpoints already take `?device_id=` and route through the resolved group | `main.py:1410`, `1442`, `1783`, `2105`, `2673`, `4222` |
| Unknown `device_id` → 404 at the HTTP boundary, uniformly | `_resolve_group_or_404`, `main.py:1394-1407` |
| `GET /api/state?device_id=X` **overwrites** `active_*` with X's group values and echoes `sync_group` | `main.py:1434-1438` |
| `POST /api/heartbeat` accepts `sync_group`; **validation rejects anything but `global` or `device:<own id>` → 400** | `main.py:3325-3332` |
| Group creation happens in exactly one place, seeded from global | `register_device` → `ensure_group`, `state.py:265-280` |
| A group is garbage when **no device claims it**; GC rides the existing prune | `gc_sync_groups`, `state.py:288-306` |
| Devices prune at a 300s heartbeat TTL | `prune_devices`, `state.py:421` |
| Per-session ttyd shipped (v0.35.0); `409 terminal_conflict` is gone | ADR header; `main.py:2130-2132` |
| `connect` still writes **server-global** `terminal_session`/`terminal_group` regardless of caller group | `main.py:2159-2161` |
| `/terminal/ws?device_id=X` closes **4409** if X's group's `active_session` ≠ the requested session | `main.py:4363-4368` |
| `GET /api/state` already returns the whole `devices` registry | `get_state`, `main.py:1431-1438` |

**The server needs almost nothing new** for the same-server case. If a deck
registers a `device_id` whose `sync_group` is `device:<browserX>`, all six
endpoints already read and write browserX's group. What's missing is **one
validation relaxation** and **the lifecycle rules that relaxation breaks**
(§7.1).

### 2.2 [REV 2] The device registry cannot distinguish a deck from a browser

| Fact | Evidence |
|---|---|
| `HeartbeatPayload` = `device_id`, `label`, `viewing_session`, `view_mode`, `last_interaction_at`, `sync_group`. **No client-kind field** | `main.py:1234-1240` |
| `view_mode` is a required `Literal["grid","fullscreen"]` — a physical deck is neither | `main.py:1238` |
| Browser labels are `navigator.userAgent.slice(0, 50)` | `app.js:204-207` |
| A device record has **no human-set name** | `state.py:176-192` |
| `label` is overwritten on **every** heartbeat | `state.py:409-418` |

**Blocks the owner's Settings-tab ask.** A dropdown listing decks and
soft-decks as distinct categories cannot be built — the server has no idea
which devices those are. A server-side rename would be clobbered within 5
seconds. Both need API work (§8.1 #6, #7).

### 2.3 Deck — zero group awareness, by design

| Fact | Evidence |
|---|---|
| **Zero functional `device_id`/`sync_group` references** in `muxplex-deck/src/` | `grep -rn` → 7 hits, all doc comments about *federation* device ids |
| Poll cycle = `sessions()` + `state()` + `settings()`, serialized on one `client_lock` | `_ActiveRuntime.refresh()` |
| Views resolved **client-side**, a port of muxplex's `filter_visible` | `muxplex_deck/views.py` |
| Key press → optimistic repaint, then background `client.connect(name)` | `connect_slot`/`_do_connect`, `main.py:1140-1200` |
| Dial 0 / VIEW key → debounced `set_active_view()` = `PATCH /api/state` **global** | `_commit_view`, `main.py:~945` |
| `MuxplexClient` has **no** `device_id` parameter and **no** `heartbeat()` | `sync_client.py:138,238,248` |
| `connect()`'s docstring already warns "active_session is server-global" | `sync_client.py:238-244` |
| Config = `server_url`, `key_file`, `ca_file`, `poll_interval`, `sort`, `controls`. **[REV 2] No `name`/`label` key** | `config.py:40-52` |
| **[REV 2]** Keys absent from `DEFAULT_CONFIG` are **silently ignored** — the `focus_app` incident | `config.py:113-140` |
| **[REV 3] `server_url` is a scalar** — a deck is a client of exactly one server | `config.py:41` |
| Hot-reloadable keys are exactly `("controls", "sort", "poll_interval")` | `config.py:382` |
| A paged chooser already exists (`PickerMode.NONE/VIEW/PAGE`) | `interaction.py:236-260` |

**Answer to the label question, definitively: no, `config.json` cannot label a
deck today.** The key does not exist, and per the `focus_app` lesson a
hand-added `"name"` would be **silently discarded**.

### 2.4 [REV 2] Everything on the deck path is federation-blind

| Fact | Evidence |
|---|---|
| The sidecar fetches `GET /api/sessions` — **local sessions only** | `sync_client.py:118` |
| The aggregated endpoint is a *different* route the sidecar never calls | `GET /api/federation/sessions`, `main.py:4888` |
| `muxplex_client.Session` has **no** `remoteId`/`deviceId`/`sourceUrl` | `models.py:84-114` |
| `ServerState` drops **`active_remote_id`** on the floor | `_protocol.py:161-167` |
| The **Soft Deck is equally blind** | `deck.js:2040-2041` |
| The PWA aggregates **only when `multi_device_enabled`** (default `false`) | `app.js:665-668`; `settings.py:77` |

### 2.5 [REV 2] The live fleet (owner-verified, read-only SSH)

| Host | Own muxplex server? | Deck? | Deck's `server_url` |
|---|---|---|---|
| **spark-1** | yes, `:8088` | — | — |
| **alienware-r13** | **yes, `:8088`, own sessions** | yes | **`https://spark-1:8088`** |
| **brians-macbook-pro-os** | **yes** (launchd) | yes | **`https://spark-1:8088`** |

**Three live servers. Two live decks. Both decks are clients of a server that
is not their own host's.**

### 2.6 [REV 3] Federation aggregation is **server-side**, and `devices` is not synced

| Fact | Evidence |
|---|---|
| **`devices` is NOT in `SYNCABLE_KEYS`** — confirmed: `"devices"` appears **0 times** in `settings.py` | `grep -c '"devices"' muxplex/settings.py` → `0` |
| `SYNCABLE_KEYS` is display prefs + session behavior only (`fontSize`, `views`, `hidden_sessions`, `sort_order`, …) | `settings.py:393` |
| **`devices` lives in `state.json`, not `settings.json`** — a different file, on a different path, with no sync mechanism at all | `state.py` module docstring; `settings.py` |
| Settings sync is **LWW full-document push**, gated on `settings_updated_at` being strictly newer (a CAS-analogue) | `PUT /api/settings/sync`, `main.py:3694-3702` |
| **The implementation DIVERGED from the original federation design.** The 2026-03-30 doc specifies "browser fetches directly from each remote (no server-side proxy)"; the shipped code fans out **server-side** via `asyncio.gather` on `app.state.federation_client` | design doc "Approach"; `main.py:4939` |
| That fan-out already has a full failure taxonomy: per-remote `status: 'unreachable' \| 'auth_failed'`, a `_federation_cache`, `_FEDERATION_GRACE_FAILURES = 3`, and a circuit breaker (threshold 3, cooldown 60s) | `main.py:4842-4855`, `4945`, `5037` |
| **A cross-server proxy WRITE precedent already exists**: `POST /api/federation/{device_id}/connect/{session_name}` proxies to a peer with Bearer auth; 404 unknown remote, 502 remote error, 503 unreachable | `main.py:5819-5860` |

**Two things follow.** (1) The owner's read is right: federated device discovery
does **not** fall out of existing sync — it needs building. (2) The pattern to
build it on already exists and is battle-tested; this is not new architecture
(§8.5).

---

## 3. Assumptions

1. One human, several screens; not multi-tenant. Adversarial deck-hijacking is
   out of scope — the deck already holds a Bearer key that can write `global`
   and yank everyone.
2. Physical decks are 1-per-host and long-lived; a persisted deck identity is
   acceptable.
3. Browser `device_id`s are **not** durable — localStorage, regenerated by a
   wipe, new profile, or private window (`app.js:392-406`).
4. The 300s device TTL stays. A laptop sleeping overnight is *normal*.
5. `active_view`, `active_session` **and `active_remote_id`** stay bundled in
   one group — which is what makes §4.5 unavoidable.
6. No new dependency, no new persistent daemon, no new port.
7. **[REV 2]** A deck is a client of exactly **one** server at a time.
8. **[REV 3]** Decks may be **re-pointed** between servers by the owner as
   normal practice (their words: they move decks between machines and mix
   registration targets). Device→server binding is **mutable over time**;
   nothing may assume it is fixed.

---

## 4. System boundaries

### 4.1 Topology as it actually is

```
  ┌─── alienware-r13 ──────────────────┬   ┌─── macbook-pro ──────────────────────┬
  │ muxplex :8088 (own sessions)│   │ muxplex :8088 (own sessions)│
  │   own devices{} registry    │   │   own devices{} registry    │
  │   own sync_groups{}         │   │   own sync_groups{}         │
  │ muxplex-deck.service ───┬   │   │ com.muxplex-deck ───┬        │
  └──────────────────────────────────────┼───┘   └────────────────────────────────┼─────────┘
                           │  server_url=spark-1         │
        ┌────────────────────────────────┴────────────────────────────────────┐
        ▼
  ┌─── spark-1 :8088 ───────────────────────────────────────────────────────────┐
  │  devices{ d-mac-tab, d-deck-alien, d-deck-mbp, ... }         │
  │  global group  +  sync_groups{ device:d-mac-tab, ... }       │
  │  frontend/  (PWA)   frontend/deck/  (Soft Deck)              │
  │  remote_instances[] ──federation──> alienware, macbook       │
  └──────────────────────────────────────────────────────────────────────────────┘
```

**A deck's *host* is irrelevant to this feature; its `server_url` is
everything.** Both decks are clients of spark-1, so spark-1's `devices{}` is
where both appear — and the only registry that will ever hold them.

### 4.2 [REV 3 — revised] Is "The server" ambiguous with 3 servers?

**Mechanically: no. As a label: yes — fix the label. And the owner's
scope-limiter makes this a hard boundary, not a preference.**

`sync_group` is per-server state in that server's `state.json`. "Global" already
means *"the shared group of the server I am talking to"* — for a tab, its
origin; for a deck, `config.server_url`. There is no cross-server sense in which
it could be read, because **there is no cross-server group storage anywhere in
the codebase.**

The owner's limiter — *"I don't need to log into the macbook PWA and follow the
spark-1 server, that's probably overkill and confusing"* — matches the
architecture exactly. **"Server (shared)" is scoped to the tab's own server,
full stop.** Not a v1 simplification; a design boundary (and §4.6 shows why
crossing it is what the deck entries would accidentally do).

Label fixes, unchanged from REV 2:
- Name the instance: **`spark-1 (shared)`**, not "The server" — available as
  `settings.device_name`, defaulting to hostname (`settings.py:46`, `556-557`),
  already public via `GET /api/instance-info`.
- Head the local device list **"Registered with spark-1"** — so an empty list on
  alienware's own PWA reads as *"no decks registered here"*, not *"broken."*
  **[REV 3]** With federated discovery this header becomes more important, not
  less: it is what distinguishes the selectable section from the informational
  one (§9.1).

### 4.3 [REV 3 — corrected] "Federation" means three things; the owner meant none of the new ones

REV 2 read the owner's "register w/ the federation" as a proposed mechanism.
**That was my misreading.** The owner's actual position: deck registration is
already solved — it is `server_url` in `muxplex-deck`'s config, chosen
per-deployment. Some decks point at a hub (spark-1); one could equally point at
its own host's local server. **Nothing to design.** Confirmed in code:
`server_url` is a plain required config field (`config.py:41`), and the deck
does exactly what it says.

The three-meanings table stands, because the *API* still conflates them and
that is worth writing down regardless:

| # | Called "federation" | What it is | Where |
|---|---|---|---|
| 1 | `federation_key` | The server's **shared Bearer credential**. Nothing cross-server about it | `sync_client.py:66-67` |
| 2 | Federation proper | **Cross-SERVER aggregation** — `remote_instances[]`, `/api/federation/*`, `multi_device_enabled` | `2026-03-30-*` |
| 3 | The device registry | **Per-server client registration** — `POST /api/heartbeat`, `devices{}`, sync groups | `2026-08-01-*` |

**The `device_id` collision is real and live:**
- `GET /api/state?device_id=d-abc12345` — a **client device** (browser-minted,
  localStorage, `d-`-prefixed, `app.js:392-406`).
- `POST /api/federation/{device_id}/connect/{session}` — a **server instance**
  (UUIDv4 from `~/.config/muxplex/identity.json`, `identity.py:12-30`).

**Same parameter name. Two disjoint namespaces. One API.** Worth a
`docs/API_SEMANTICS.md` entry regardless of whether this ships.

**[REV 3] Revised conclusion.** REV 2 said the owner's idea "needs #3, not #2."
That was half right and needs splitting:
- **Registration and selection** need **#3 only** — per-server, no federation.
- **Discovery** (which decks exist, to populate the picker) now needs **#2**,
  because a server has zero visibility into a peer's `devices{}` (§2.6).

So federation *is* genuinely involved — on the read path only, and only because
the owner's fleet spreads registrations across three real servers.

### 4.4 What's in / out

**In:** group resolution, the per-server device registry, deck identity, deck
and browser UX, the `active_remote_id` hazard, **[REV 3]** read-only federated
device discovery.
**Out:** cross-server *following* (§4.6 — not merely deferred; not expressible),
the terminal WS relay, auth, federation settings sync.

### 4.5 [REV 2] The federation hazard that IS in scope — and is live today

Chain, every link verified:

1. `active_remote_id` is a **`GROUP_FIELD`** (`state.py:122`). A group means
   *(which session, **on which server**, in which view)*.
2. Sync groups store per-device (§2.1).
3. A deck in `device:d-mux-browser-tab` has copied that browser's remote context.
4. The deck's screen doesn't render `active_remote_id` or route to federation
   (§2.4).
5. A key press → `connect(session_name)` on the *deck's* server, not the
   browser's.
6. A remote-server session name, pressed into the wrong server, → `404
   session_not_found`.
7. The result: the deck **highlights nothing on its screen, silently falls back
   to global's first session**, renders it (a wrong session), and nobody knows.

**This is a silent data hazard, live today.** The v1 fix is mandatory before a
deck can own a foreign group (§7.1). REV 2 diagnosed it; REV 3 confirms it is
the blocker to ship, not an edge case to defer.

---

## 5. What works right now (nothing changes)

| User move | What really happens today |
|---|---|
| Browser tab goes independent on spark-1 | Tab joins `device:d-browserX`. Deck remains in global. |
| Browser tab on alienware's PWA, viewing spark-1 sessions via federation | Tab stays on alienware-r13 server, joins `device:d-alienware-browser`. Fetches from spark-1 via federation. Session/remote_id can both be spark-1 values. Deck on spark-1 remains in global. |
| Deck presses a key | Always talks to spark-1. Always writes `global`. Both patterns above: deck remains in global, silently. |
| Deck was following the browser tab (hypothetical, not possible today) | Would be following `device:d-browserX` on spark-1. Presses still reach spark-1. But a browser on alienware with a *different* device id fetching remote sessions — that is a **different** group. Deck can't follow it. |

**§4.5 captures why this is hazardous now.** The feature makes it possible to
deliberately pair them. The hazard already exists *if* discovery ever listed a
tab from a different server.

---

## 6. Components and control flow

### 6.1 [REV 2] Server-side data model  

Add to `state.json`:
```json
"devices": {
  "d-abc123": {
    "device_id": "d-abc123",
    "label": "Chrome on spark-1",
    "kind": "browser",         // [REV 2] new
    "display_name": "My iPad",  // [REV 2] new, overrides label
    "sync_group": "global",
    "viewing_session": null,
    "view_mode": "fullscreen",
    "last_interaction_at": 1694785543.0,
    "last_heartbeat_at": 1694785543.0,
    "controlled_by": null  // [REV 2] new: device_id of whoever is following me
  }
}
```

What changes:
- `kind` — added (§8.1 #6)
- `display_name` — added (§8.1 #7)
- `controlled_by` — added (§8.1 #2)

### 6.2 Client-side (browser + deck) data flows

#### 6.2.1 Browser

1. **Render loop** — `renderSyncGroupControls()` in `frontend/app.js:551-600`
   already reads state. Keeps doing so. New: it renders a dropdown instead of
   a toggle (§9.4).
2. **Selection** — user picks a target from the dropdown. Sends `PUT /api/state`
   with `sync_group`, or omits it to mean "client-local."
3. **Render** — the `controlled_by` chip shows "Controlled by: Stream Deck
   (alienware)" if a deck is following this tab (§7.3).

#### 6.2.2 Deck

1. **Config** — `muxplex-deck` already reads `server_url`. New: reads `name` if
   present (§8.4) and sends it in the heartbeat as `label`.
2. **Heartbeat** — new: includes `device_id`, `kind`, and `sync_group` (§8.1
   #1).
3. **State poll** — `state(device_id=self.device_id)` returns the deck's own
   group; render from that (§8.3).
4. **Connect** — `connect(name, device_id=self.device_id)` writes the deck's
   group (§8.3).
5. **Soft Deck** — adds `device_id` storage in localStorage, `kind: "soft-deck"`,
   `sync_group` tracking, and the new dropdown (§6.2.10, §9.3).
6. **Physical Deck** — adds a `device_id` to persisted config, stores `sync_group`
   at heartbeat time (§6.2.4 detail).

#### 6.2.3 [REV 2] Client-local pinning for "None"

When `sync_group` is `null` or "local-only," the browser doesn't send a new one
on heartbeat — it uses the *previous* `sync_group` if present, or `global` as a
fallback. Server-side, nothing changes: the device still holds *a* sync_group.
Client-side, a `null` in state means "ignore server updates on my own session
selection; I choose what I render." Dial/view key presses don't move the server
state at all (§7.0(a)).

#### 6.2.4 [REV 2] Deck lifecycle and the sticky-group problem

The **problem:** A `muxplex-deck` config lives on alienware-r13, sends heartbeat to spark-1 with `sync_group: "device:d-mux-browser-123"`. Browser tab on spark-1 with id `d-mux-browser-123` goes away (close tab, localStorage wipe, 300s prune). The device entry vanishes. The deck's heartbeat tries to claim a now-nonexistent group.

**Solution:** Decks **always send their current `sync_group` on heartbeat**, but validation (#1 in §8.1) has one change — a deck may **claim a group it doesn't own, but only once per startup** (i.e., resume the group from the last run; don't auto-create). If the target is gone, `POST /api/heartbeat` returns `409 target_gone`, and **the deck falls back to global** and **renews the heartbeat there** (repeating this in the same connection is a no-op).

This is the **only state the deck ever stores durably**: which group it *tried* to be in. If that group is gone, the deck knows to return to global and rebind. No silent hangs. No 500 errors. Clean retry loop.

#### 6.2.5 [REV 2] Preventing cycles

When a browser follows a deck, and (somehow) the deck tries to follow that
browser, we have a cycle. It's **not a hang** (§2.1 — resolution is one hop,
never transitive). It is **a silent state error** — two clients claim each other,
group membership becomes undefined.

**Guard (#8 in §8.1):** A device with non-empty `controlled_by` may never
successfully send a `sync_group != "global"`. Validation rejects it with `400
target_not_self_owning`, naming the device that is following this one.

#### 6.2.6 [REV 2] What "None" actually means

**"None"** (client-local pinning) is **not** a server-side group. It is:
- Browser doesn't send a `sync_group` on heartbeat.
- Browser keeps rendering the session it has, unchanged.
- Dial 0 (view-select key) does **not** issue a `PATCH /api/state`.

*Result:* no server-state mutation. No group to prune. No lifecycle cost. Clean
escape hatch.

#### 6.2.7 [REV 3] Federated discovery: render local, fill async

The browser's dropdown is built in two phases:
1. **Immediate:** query `/api/state?device_id=<own>` for the local device registry. Render the two local sections (escape hatches and registered-here devices).
2. **Background:** `GET /api/federation/devices` fans out async. Build the "Elsewhere in your federation" section.

**Never block the control on a peer being slow or down.** The federated section populates and updates independently.

#### 6.2.8 [REV 3] Multi-location deck identity

A deck moves from spark-1 to alienware-r13's own server (re-pointed via config change). During the 300s window before spark-1's GC prunes the old entry, **the picker shows two entries**:
- "Stream Deck (alienware) — via spark-1" (the stale one)
- "Stream Deck (alienware) — via alienware" (the new one)

**Fix:** Client-side picker keying, not server-side. A federated device list entry is keyed `<home_device_id>:<client_device_id>` (borrowed from the `device_id:name` convention in auto-views). Render the home-server name as a suffix. The UI shows one logical device in two locations; picking the new one writes there; the stale picker entry disappears when GC runs (§9.1).

#### 6.2.9 [REV 3] Non-selectable federated section: the "Open on X" link

An informational device listed under "Elsewhere in your federation" (e.g., a
deck registered on alienware, viewed from spark-1's PWA) is not selectable. It
shows a link action: "Open on alienware." Clicking navigates to that server
(via `GET /api/federation/sessions` to find its URL if needed, or from
`remote_instances[]` if already known).

#### 6.2.10 [REV 3] Degraded federation: unreachable peers are shown, not omitted

A peer is unreachable (network down, auth failed, timeout). The "Elsewhere in
your federation" section still renders it, but:
- The entry is un-clickable.
- A status chip: "⚠️ Couldn't reach alienware-r13" with a "[Retry]" action.

This is **the anti-pattern fix to the v0.48.3 bug**: state that requires a hover
to discover is not state the user has. The widget shows degradation explicitly
in closed form.

---

## 7. Risk categories and mitigations

### 7.0 Three safety rules

**(a) "None" is never a server group.** Client-local state has zero
server-side footprint: no group to garbage-collect, no lifecycle to guard. This
is what makes it an escape hatch.

**(b) Cycles are impossible.** A device with `controlled_by != null` cannot
claim a foreign group. Validation rejects it at the HTTP boundary.

**(c) No cross-server write.** A deck's `sync_group` change always happens on
the deck's own server (its `server_url`). Full stop.

All three are load-bearing. If any one breaks, the whole design becomes
unreliable.

### 7.1 The `target_gone` guard is a ship-blocker for physical deck

**The scenario:** A paired deck is told to follow browser tab `d-abc`. Later,
that tab is closed or localStorage is wiped. `d-abc` is pruned. The deck's next
heartbeat sends `sync_group: "device:d-abc"` but the group is gone.

**What happens today (deck cannot own a foreign group yet, so this is hypothetical):**
`resolve_group()` → `KeyError` → 404 `_resolve_group_or_404` → deck sees 404
and (today) retries unchanged. With pairing, the deck would see 404 and **need
to handle it gracefully** — backoff and retry, or fall back to global. **This is
non-trivial; it is not automatic.**

**The fix:** Add `409 target_gone` response on heartbeat when a deck's claimed
group doesn't exist (§8.1 #5). Deck client code handles 409 → fall back to
global, renew heartbeat. This is **a mandatory part of Step 2** (§10).

**Proof required in Step 2's DTU test:** pair a deck → let the target's TTL
expire → heartbeat → **verify 409, no 500, deck moves to global automatically**.

### 7.2 [REV 2] Remote-session mis-connection is a render hazard

§4.5 describes the hazard: a deck following a browser that is viewing a remote
server. The deck's connect writes to its own server, not the browser's, which
can yield a name collision and a silent wrong-session connection.

**The v1 fix (REV 2 ship-blocker for Step 5):** when `active_remote_id != null`
(meaning the browser's session comes from a peer):
- **On the deck:** suppress highlight of the selected session (render all
  sessions as unselected).
- **On the browser:** show a chip: `> remote (alienware)`.
- **In the picker (deck hardware or Soft Deck):** don't offer the deck a choice
  while this state exists.

All three send the same signal: "something is broken; pause here."

**The v2 fix (post-v1, much larger change):** teach the deck to aggregate
sessions via `/api/federation/sessions`, carry the `active_remote_id` through
session rendering, and route `connect()` via the federation proxy endpoint.
This is a rewrite; too late for v1.

### 7.3 [REV 2] Informational "controlled by" chip

When a deck is following a browser tab, the browser renders a persistent,
un-dismissable chip: **"Controlled by: Stream Deck (alienware)"** or **"Controlled by: Soft Deck"**.

Why this matters: without it, a user on the browser doesn't know the deck has
claimed their selection. They think their presses are free; the deck claims them
silently. This is the flip side of the original bug (deck silent, user confused).

The chip is rendered in the header, next to the sync-group dropdown. Not a
button; no action. Just "here's what's following you."

---

## 8. API / implementation

### 8.1 Implementation summary — ten changes + two guards + one endpoint

| # | Component | Change | Impact |
|---|---|---|---|
| **1** | `POST /api/heartbeat` | **`sync_group`** parameter already accepted; validation now allows string values that start with `"device:"` and match the calling device's own id (self-claim only) | Unlock same-server pairing |
| **2** | `devices` registry | Add **`controlled_by`**: null or a device_id naming whoever is following me. Set by validation #1 | Half of the cycle guard |
| **3** | `PATCH /api/devices/{device_id}` | New. Accepts `display_name` (human label, survives heartbeat). Local-only writes (no federation proxy yet) | Let humans name decks |
| **4** | Group GC (`state.py:288-306`) | **Fix:** a group is garbage when **no *device* claims it**. Current code checks `controlled_by` naively; a device that *controlled others* is treated as a consumer. Reverse the polarity: a device with `controlled_by` is a *follower*, not a holder | Prevent orphaned groups when a pair breaks up |
| **5** | `POST /api/heartbeat` | Return **`409 target_gone`** if the claimed group doesn't exist (and it's not a fresh `register_device` call) | Clean deck fallback path |
| **6** | `POST /api/heartbeat` | Optional **`kind: "browser" \| "deck" \| "soft-deck"`**, default `"browser"` | Additive; absent → today's only client class |
| **7** | `PATCH /api/devices/{device_id}` | Also accepts **`display_name`** — server-side label the heartbeat **never** overwrites | Solves the §2.2 clobber: `label` = client self-report, `display_name` = human's |
| **8** | validation, #1 + #3 | Target must be **self-owning** → else **400 `target_not_self_owning`**. A device with non-empty `controlled_by` may not start following → **400** naming followers | Implements §7.0(b); unreachable for current clients |
| **9** | `GET /api/state` | `devices` entries expose `kind` and `display_name` | Additive |
| **10** | `muxplex_client` | Parse **`active_remote_id`** into `ServerState` | Additive; §4.5 v1 ship-blocker |
| **11** | **[REV 3]** `GET /api/federation/devices` | **New, read-only.** Server-side fan-out to `remote_instances`, returning a **filtered projection** per device: `device_id`, `display_name`, `kind`, `last_heartbeat_at`, `sync_group`, plus `homeDeviceId`/`homeDeviceName` and a per-peer `status`. Never the full device record | New endpoint. Empty/absent `remote_instances` → local-only, so a non-federated server behaves exactly as today |
| **12** | **[REV 3]** picker entry identity | Federated entries keyed **`<home_server_device_id>:<client_device_id>`** | Not a wire change — a client-side keying rule. Mirrors the existing `device_id:name` view-membership convention (§6.2.8) |

**No change** to: `StatePatch`, `/terminal/ws`'s guard, `connect`'s signature,
auth, `remote_instances`/aggregation semantics, `SYNCABLE_KEYS`, or the
no-`device_id` default path.

**[REV 2] Deliberately NOT changed: `view_mode`'s
`Literal["grid","fullscreen"]`.** A deck is neither, but adding `"deck"` would
422 on every older server. **Decks send `view_mode: "grid"` and declare
themselves via `kind` (#6).**

**[REV 3] Deliberately NOT built: a cross-server device-registry write.**
`PATCH /api/devices/{id}` never proxies. §4.6 shows a proxied write would not
make cross-server following work anyway (the follower's *read* is the blocker),
so shipping one would add a write path that buys nothing and implies a
capability that does not exist.

### 8.2 Version tolerance, both directions

- **Old client → new server.** No `device_id` → `global`. Byte-identical to
  today (ADR §4.0/§8), re-proven by regression armor (ADR test #14).
- **New client → old server.** An old server 400s on a foreign `sync_group` and
  **may 422 on unknown `kind`** — `HeartbeatPayload` is a plain `BaseModel`, so
  **verify its extra-field behavior before assuming `kind` is ignored**; if it
  rejects, don't send `kind` until the version is confirmed.
  `MuxplexClient.check_server(min_version)` (`sync_client.py:452`) is the gate —
  bump `MIN_SERVER_VERSION` for the *pairing* path only, never basic operation.
  Degrade to global with an honest message; **never loop.**
- **[REV 3] New server → old peer.** A peer without #11 returns 404. Treat it
  exactly as `unreachable`/`auth_failed` are treated today: that peer's decks
  are absent and **the UI says so** (§6.2.10). Never silently omit.
- **Old `state.json` → new server.** No migration; `sync_group` normalizes to
  `"global"` (`state.py:364`), `kind`/`display_name` absent-safe.

### 8.3 `muxplex_client` — the missing rung

Today: **no `device_id` anywhere, no `heartbeat()`.** Additive, keyword-only:

```python
def heartbeat(self, *, device_id, label, viewing_session=None,
              view_mode="grid", last_interaction_at=0.0,
              sync_group=None, kind=None) -> HeartbeatResult: ...

def state(self, *, device_id: str | None = None) -> ServerState: ...
def view(self, *, sort=None, device_id: str | None = None) -> ViewResult: ...
def connect(self, name, *, device_id: str | None = None) -> ConnectResult: ...
def set_active_view(self, view, *, device_id: str | None = None) -> None: ...
```

`ServerState` gains `sync_group`, `controlled_by`, **and `active_remote_id`**
(#10), all via `.get()`. Map `409 target_gone` → `TargetGoneError(ApiError)` and
`400 target_not_self_owning` → its own type. **Update `connect()`'s docstring** —
"active_session is server-global" stops being unconditionally true.

### 8.4 [REV 2] `muxplex-deck` config

Add **`"name": ""`** to `DEFAULT_CONFIG` (`config.py:40-52`) and to
`RELOADABLE_KEYS` (`config.py:382`); empty → derive from hostname. Sent as
`label`.

**Must go in `DEFAULT_CONFIG`, not merely be read** — per §2.3, absent keys are
silently ignored, the `focus_app` failure mode.

**Where the label lives:** deck config `name` is the **default**; the server's
`display_name` (#7) is the **override** and wins — renaming a deck shouldn't
require SSH-ing to a different physical machine than the server you're looking
at.

### 8.5 [REV 3] Mechanism decision: pull-on-demand fan-out, NOT settings sync

The owner offered three routes. **Recommendation: a read-only fan-out endpoint
(#11) modeled directly on `/api/federation/sessions`** — same
`asyncio.gather`, same `app.state.federation_client`, same Bearer-per-remote,
same `_FEDERATION_GRACE_FAILURES`/cache/circuit-breaker, same per-peer status
vocabulary (§2.6).

**Why not extend `SYNCABLE_KEYS` settings sync** — three grounded reasons:

1. **Wrong file.** `SYNCABLE_KEYS` governs `settings.json`. `devices` lives in
   `state.json`, which has **no sync mechanism at all**. This isn't "add a key";
   it's "build a second sync path."
2. **Wrong data model.** Settings sync is **LWW full-document push** gated on
   `settings_updated_at` (`main.py:3694-3702`) — built for *convergent config*.
   A device registry is **presence data**: 300s TTL, per-heartbeat churn,
   authoritative only on its home server. Pushing it through LWW would bump
   `settings_updated_at` continuously — **the exact coupling `views_updated_at`
   was introduced to break** (`settings.py:217-220`) — and let one peer's stale
   snapshot clobber another's live registry.
3. **It would materialize a peer's resolved state into local storage** —
   the pattern `AGENTS.md` has a standing prohibition against in the auto-views
   rule ("rules stay rules on disk, forever; resolve membership fresh on every
   read"). A peer's registry is the same shape: **resolve fresh, never
   materialize.** Materializing would reintroduce decay, turn every poll into a
   settings write, and hand federation LWW a new race — the three costs that
   rule names explicitly.

**Also considered and rejected: browser-direct fan-out** (the *original*
2026-03-30 design). The shipped implementation already moved to server-side
(§2.6); doing discovery browser-side would mean the Soft Deck and PWA each
re-implement peer iteration, auth and failure handling — the client-drift
pattern `AGENTS.md` warns about, paid twice.

---

## 9. UX

### 9.1 [REV 3] The dropdown — two sections, two meanings

**Label.** "Follows" for the actor's own setting (the widget sets what **I**
follow); reserve "Controlled by" for the reverse-direction chip (§7.3). Using
each for its own direction is clearer than picking one for both.

```
Follows:
  ◉ spark-1 (shared)                    ← this tab's own server (§4.2)
  ◯ Nothing — just me                   ← client-local (§7.0(a))
  ──────────────────────────────────────
  Registered with spark-1               ← SELECTABLE
  ◯ Stream Deck (alienware)
  ◯ Soft Deck — iPad
  ──────────────────────────────────────
  Elsewhere in your federation          ← INFORMATIONAL, not selectable
    Stream Deck (studio) — via macbook     [Open on macbook ↗]
    ⚠ Couldn't reach alienware-r13         [Retry]
```

Load-bearing details:
- **Escape hatches first**, never alphabetical.
- **The two sections must be visually and semantically distinct** — the
  informational one gets no radio, a home-server suffix, and a link action
  (§6.2.9).
- **Render local-first, fill federated async** (§6.2.7) — never block the
  control on a peer.
- **Unreachable peers are shown, not omitted** (§6.2.10), reusing the endpoint's
  existing `unreachable`/`auth_failed` statuses.
- **Degraded state in the closed widget**, not behind a hover — the whole lesson
  of the v0.48.3 bug: `Follows: Stream Deck (alienware) — offline`.
- **A moved deck reads as one deck in two places** via the `<home>:<id>` key and
  the "via X" suffix (§6.2.8).

### 9.2 Physical deck — the strip is the honest signal

The bug just fixed (a link icon rendering *un*-selected while following) is the
same failure class as the core complaint: **state that requires a hover to
discover is state the user does not have.**

- **Strip headline** (`main.py:374-393`) already reads
  `view · p1/2 · spark-1 · 12 sessions · ACTIVE: foo`. Append `> shared` or
  `> MacBook`. Keep it **ASCII** — the device's default PIL font renders `→` as
  a `.notdef` box (`main.py:380-383`).
- **Target picker**: a `target_picker` action + `PickerMode.TARGET` reusing the
  existing paged chooser. Not bound by default — a reserved key is expensive.
  **[REV 3]** Lists **local-registry devices only**. A deck cannot meaningfully
  offer a target it cannot follow (§4.6), and it has no screen to explain a
  greyed-out entry.
- **[REV 2] Remote-session degraded state (§4.5 v1):** `active_remote_id`
  non-null → strip reads `> remote (alienware)`, highlight **suppressed**.
- **Pinned view ("None")**: prefix the label — `[work]` vs `work`.

### 9.3 Soft Deck — prototype the UX here first

`deck.js` is a browser tab with a settings panel: it can render the dropdown in
an afternoon, zero hardware, zero sign-off ritual. **Build G here first, port
the proven model to the LCD picker.**

Constraint: deck.js settings are deliberately localStorage-only, never
server-synced (`deck.js:295-317`). A pairing target is *server* state, so a
heartbeat doesn't violate that — but follow ADR §7.1's lesson: store the *mode*,
never a resolved group id (a regenerated `_deviceId` strands `"device:d-old"`).
A *foreign* target is the exception that must store an id — which is why §7.2's
sticky-and-loud policy exists.

### 9.4 [REV 2] Browser — the dropdown replaces the toggle

`#sync-group-btn` / `#sync-group-btn-expanded` (`index.html:761-769`) become the
`<select>`, styled `.settings-select`/`.sidebar-select`, kept in sync by the
existing `renderSyncGroupControls()` pattern.

⚠️ **`test_frontend_js.py` is a source-text tripwire** (`AGENTS.md:1118-1140`).
Removing the button and its
`classList.toggle('header-btn--active', ...)` — the line fixed in v0.48.3 —
**will** trip assertions. Per the documented rule: if behavior is preserved,
update the assertion to assert the new structure; **never loosen it.**

### 9.5 [REV 2] Settings → the new tab

**Naming collision:** a **"Multi-Device"** tab already exists
(`index.html:349`, `data-tab="devices"`) meaning **federation**. Putting
"Control Decks" beside it renders the §4.3 conflation directly in the UI.

**Recommendation: name the new tab plain "Decks."** Do **not** rename
"Multi-Device" in the same change.

Contents:
1. **"Open Soft Deck"** → `/deck/`. Verified reachable: frontend mounted at `/`
   with `html=True` (`main.py:6133`) over `_FRONTEND_DIR`; the deck lives at
   `frontend/deck/`. A plain link, no new route.
2. **"Set up a physical Stream Deck"** → the muxplex-deck project.
3. **Registered devices list** — `display_name` (inline-editable → #7), `kind`
   badge, what it Follows, last-seen; headed **"Registered with spark-1."**
   **[REV 3]** Plus a second section, **"Elsewhere in your federation"**,
   from #11 — same split and same rules as the picker (§9.1). Needs #6, #7, #9,
   #11; **cannot be built before them.**

---

## 10. Build sequence, riskiest-unknown first

**Step 0 — Ship A (client-local view pinning), framed as "None."** No API, no
server. Stops the deck yanking every browser on every dial turn. If nothing else
ships, this still pays.

**Step 1 — Walking skeleton: identity + labels + kinds, everyone stays global.**
`muxplex_client.heartbeat()` + `device_id=` passthrough; deck mints and persists
a device id; deck sends `?device_id=` on every group-touching call **while
remaining in `global`**. Land `kind` (#6), `display_name` (#7), `name` in
`DEFAULT_CONFIG` (§8.4), and `active_remote_id` parsing (#10).
**Behavior must be byte-identical to today** — that is the whole test.

**Step 2 — Server: relax, and repair what relaxing breaks.** Validation
relaxation (#1) + self-owning/cycle guard (#8) + GC self-claim fix (#4) +
`target_gone` guards (#5) + `controlled_by` (#2) + `PATCH /api/devices` (#3,
local-only). Prove in the DTU with a scripted multi-client test modeled on the
ADR's `scripts/proof_sync_groups.py`: **pair → prune target → no 500, no silent
global**, and **attempt a cycle → 400 naming the other party.**

**Step 3 — Soft Deck dropdown** (zero hardware) — validate ordering, wording,
degraded state. **Local section only.**

**Step 4 — PWA: replace the toggle with the dropdown + `controlled_by` chip +
the "Decks" settings tab.** Where the reported complaint gets cured. Budget for
the `test_frontend_js.py` tripwire (§9.4).

**Step 5 — Physical deck**: strip target indicator + **§4.5 v1 remote-session
suppression (ship-blocking)**, then the opt-in `target_picker`. Mandatory
real-hardware sign-off — the emulator has missed real device behavior before.

**[REV 3] Step 6 — Federated discovery (H).** `GET /api/federation/devices`
(#11) + the informational picker/Settings section + `<home>:<id>` keying (#12).

**Why last, deliberately:** every deck in the owner's fleet is registered with
spark-1 today, so **Steps 1-5 are the complete feature from a spark-1 tab** —
Case 1 in §4.6, no federation involved. Step 6 buys visibility from
alienware's and macbook's *own* PWAs, and correctness the day a deck gets
re-pointed. Real value, but strictly after the thing it decorates works.
It is also cleanly droppable if Steps 1-5 turn out to be enough.

### Riskiest unknowns, ranked

1. **The federation mis-highlight (§4.5).** The only unknown that can make a
   deck *connect the wrong machine's session*, and all three servers are live
   now. **v1 suppression is a ship-blocker for Step 5.**
2. **Target lifecycle.** The 500 in §7.1 is a direct read of
   `main.py:1435-1436` plus the GC fix. Guards land in the *same* PR as the
   relaxation.
3. **[REV 3] Device→server binding is mutable (assumption 8), and device ids now
   have to be unique across N registries, not one.** REV 2 flagged localStorage
   churn; this compounds it — a re-pointed deck is legitimately in two
   registries at once, and a browser id is only unique within its own server.
   The `<home>:<id>` key (#12) makes the composite explicit; whether that is
   *sufficient* in daily use is genuinely unproven. **If this bites often, it is
   the strongest evidence yet for Alternative F.**
4. **[REV 2] The dropdown's option set outgrowing its explanation.** Cycles,
   stale tabs, browser→browser, **and now a two-section list**. Guards are
   §7.0(b)/(c) and §9.1; sufficiency is unknown until Step 3 is in a human's
   hands.
5. **`connect` writes global `terminal_session`/`terminal_group`
   unconditionally** (`main.py:2159-2161`). A paired deck's press still moves the
   server-wide no-`?session=` fallback. Post-per-session-ttyd this is *probably*
   benign — **verify, don't assume.**
6. **Deck threading.** The heartbeat must ride the existing poll tick under the
   existing `client_lock` — a second HTTP thread against one `httpx.Client` is
   exactly the concurrency the module docstring avoids.

### Parked deliberately (good, not now)

- **[REV 3] Cross-server *following*** (replicated group state or a per-poll
  proxy read). §4.6 shows it is not a missing endpoint but a missing storage
  model, and that its only coherent meaning is the cross-server server-follow
  the owner ruled out. Revisit only if the scope-limiter changes — and price it
  as Alternative F, not as a dropdown tweak.
- **[REV 3] "Re-point this deck here"** from the picker. Matches the owner's real
  workflow, but needs a remote write to a deck's *own config file* on a third
  machine — a new remote-config surface with no precedent. Today that's an SSH
  + `muxplex-deck config set server_url`.
- **[REV 2] Full federation-aware deck rendering (§4.5 v2)** — poll
  `/api/federation/sessions`, carry `remoteId` through `Session`, connect via
  `POST /api/federation/{id}/connect/{name}`. The right long-term answer; also a
  rewrite of the deck's fetch/render/connect path. Step 5's v1 makes the deck
  **honest** without it.
- **`GET /api/view?device_id=` replacing the deck's `views.py` port.** The
  direction `AGENTS.md` prefers, but a render-pipeline rewrite mid-feature —
  and **also blind to remote sessions**, so not a substitute for the above.
- **F, control channels.** Revisit if risk 3 proves chronic.
- **Auto-follow (E) as an opt-in dropdown entry.** Cheap *after* explicit
  targeting exists to compare against. Never the default.
- **[REV 2] Renaming the "Multi-Device" settings tab** (§9.5).

---

## 11. Success metrics

1. **Regression armor holds**: all six endpoints, no `device_id`,
   value-identical to a `main` baseline (ADR test #14). Non-negotiable.
2. Lifecycle: pair → prune target → **409 `target_gone`**, no 500, no silent
   global fallback, group collected.
3. Cycle attempt → **400 `target_not_self_owning`** naming the other party; no
   orphaned group.
4. Turning dial 0 with "None" selected issues **zero** `PATCH /api/state`.
5. Deck following tab X: a key press changes X's session; a *different*
   global-group tab does **not** move.
6. Tab X shows a "controlled by" indicator without hovering.
7. With tab X viewing an alienware session via federation from spark-1, a
   following deck shows `> remote (alienware)` and **highlights nothing.**
8. Settings → Decks lists both physical decks, correctly named and typed, under
   a header naming spark-1.
9. **[REV 3]** From **macbook's own PWA**, both spark-1-registered decks appear
   under "Elsewhere in your federation," are **not** selectable, and carry a
   working "Open on spark-1" link.
10. **[REV 3]** With alienware powered off, macbook's picker **says it couldn't
    reach alienware** rather than silently listing fewer decks.
11. **[REV 3]** A deck re-pointed from spark-1 to macbook appears as **one deck**
    (two locations) during the ≤300s overlap, never as two unrelated decks.
12. Real-hardware sign-off on the strip indicator and picker.

## 12. What would have to be true for this to be the wrong choice

- **If browser device ids churn often** — the stored foreign id is a permanent
  papercut and F was right from the start. *Signal:* how often re-pairing is
  needed after a month.
- **If the dropdown's extra options go unused** — if only "shared" and "None"
  are ever selected, G bought vocabulary and reversibility cost for nothing, and
  a **three-state** control (Shared / None / Paired-to-last) was the cheaper
  clarity fix. *Signal:* count distinct targets ever selected after a month.
  **Still the most likely way G is wrong.**
- **[REV 3] If the federated section is never used to do anything** — if in
  practice the owner always walks to the machine or re-points the deck, Step 6
  bought a cross-server read dependency for an inventory display. *Signal:* is
  the "Open on X" link ever clicked? **This is the most likely way H is wrong,
  and it is cheap to find out because Step 6 is last and droppable.**
- **[REV 3] If the scope-limiter reverses** — if the owner later *does* want to
  follow spark-1 from macbook's PWA, §4.6's wall becomes the thing to solve, and
  this design's local-selection rule is the first thing to revisit. It would be
  a much larger project, and should be recognized as one rather than smuggled in
  as "make the deck entries selectable."
- **If the real desire is "follow whatever screen I'm at"** — E is the feature
  and pairing is ceremony. *Signal:* the target is changed manually more than
  ~weekly.
- **If Step 0 alone removes the pain** — a meaningful share of the irritation may
  be *the deck yanking browsers on dial turns*, not the divergence. **Ship
  Step 0 and wait a week before Step 2.**
- **[REV 2] If the fleet consolidates to one server** — much of §4's care becomes
  dead weight. The trend is the other way, so design for the fleet that exists.

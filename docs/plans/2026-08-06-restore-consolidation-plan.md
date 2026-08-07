# Restore Consolidation — Design Spec

**Status:** design only, nothing implemented.
**Date:** 2026-08-06 · **Host measured:** `spark-1` · **muxplex:** v0.41.0

---

## VERDICT — read this first

**Delete the dotfiles pair and its three systemd units. Add nothing to muxplex. Do not
build a boot-time replacement.**

Concretely — five deletions, zero additions:

| # | Delete | Why |
|---|--------|-----|
| 1 | `~/dotfiles/bin/amplifier-workspace-snapshot` | Its record is a *mirror of the present*, not a record of presence. Structurally incapable of surviving a mass-loss event. |
| 2 | `~/dotfiles/systemd/user/amplifier-workspace-snapshot.timer` | Nothing left to schedule. |
| 3 | `~/dotfiles/systemd/user/amplifier-workspace-snapshot.service` | Nothing left to run. |
| 4 | `~/dotfiles/bin/amplifier-workspace-restore` | Its restore behavior is a strict subset of `muxplex restore`, minus the fidelity check — and its boot path is **verified armed to destroy the sessions it creates** (§4). |
| 5 | `~/dotfiles/systemd/user/amplifier-workspace-restore.service` | Same. This unit is the destroyer, not the script. |

Plus: archive (do not delete) `~/.local/state/amplifier-workspace/active` until Gate G4 passes.

**Nothing is added to muxplex.** No new `/api/*` surface, no new manifest field, no new
flag, no boot unit, no sync layer. muxplex v0.41.0 already records a strict superset of
the dotfiles record, with identical content, under a structurally correct guard.

**The one capability that genuinely goes away, stated plainly rather than buried:**
automatic session restore after a reboot. Read §5 before accepting that — the short
version is that this capability has never once worked on this machine, and §4 shows that
the first time it *did* fire at a real boot it would have killed every session it had just
rebuilt. It is not a capability being traded away; it is a loaded gun being unloaded. The
replacement is a documented two-line human procedure (§6), rehearsed under Gate G3.

---

## 1. The bug, named precisely

Two records is the presenting symptom. The actual defect is narrower and worth stating in
one sentence, because it determines which record dies:

> `sessions.json` records **what has been alive, under which tmux server identity**.
> `active` records **what is alive right now**, and overwrites itself with that answer
> every five minutes.

A record of the second kind cannot survive the event it exists for. The moment that
destroys 52 sessions produces an observation — "few or no sessions" — that is
*indistinguishable* from the observation produced by a quiet afternoon. The snapshot
script knows this and defends with a threshold:

```bash
if [ "${#sessions[@]}" -eq 0 ]; then exit 0; fi
```

**Every threshold fails at threshold + 1.** This one failed at exactly 1, on the first
manual session started to begin recovery. Raising it to 5, or to "50% of last known", or
to "N of the previous count" moves the cliff without removing it, and each variant fails
on a real, reachable sequence of events. There is no correct constant here because the
signal being thresholded — *how many sessions do I see* — does not contain the
information needed. The information needed is *am I looking at the same tmux server I was
looking at last time*, and the snapshot script never asks it.

`manifest.py` already asks it, and its module docstring already wrote down why, after the
2026-07-29 incident:

> *"Positive record, not negative — the whole fix in one sentence: an entry is removed by
> exactly one thing — observed individual death against a live, identity-matched server."*

The snapshot script is `pruning.json`'s trap wearing different clothes: it tracks the
present rather than absence, but it is removed by *the passage of five minutes*, which is
precisely the "removed by something other than observed individual death" failure
`manifest.py` was built to never repeat.

**This is why the consolidation cannot be a sync layer, a merge, or a "prefer the larger
record" rule.** Any of those preserve a writer that destroys the record on a schedule.
The record must have exactly one writer, and it must be the one whose removal rule is
`observed individual death against a live, identity-matched server`.

---

## 2. Evidence: the two records, measured on this host today

All figures below were read off the live machine on 2026-08-06, not inferred.

### 2.1 Scope — the manifest is a strict superset

```
manifest sessions:  60      (~/.local/share/muxplex/sessions.json)
snapshot entries:   56      (~/.local/state/amplifier-workspace/active)

IN SNAPSHOT, NOT IN MANIFEST:   0      ← nothing is lost by deleting the snapshot
IN MANIFEST, NOT IN SNAPSHOT:   4
    attention-manager   -> /home/bkrabach
    cos-gateway         -> /home/bkrabach/dev/better-attention/voice-chief-of-staff
    relay-probe-15dc    -> /home/bkrabach/dev/better-attention/lane-demo
    vcos-review         -> /home/bkrabach/dev/better-attention/voice-chief-of-staff
```

Those four are exactly the class the 2026-08-05 incident was about: hand-started
long-running daemons rooted outside `~/dev/<name>`. The snapshot's comment calls them
out of scope because they "are not declaratively rebuildable." That is a defensible
scoping decision for a *restorer*. It is the wrong scoping decision for a *record* —
the sessions least reconstructible from their name are exactly the ones you most need to
be told the names of. muxplex remembers all four and, at restore time, refuses them **by
name, with their real directory in the message** (§3.3). The snapshot forgets they ever
existed.

### 2.2 Content — the two cwd mechanisms agree, 56 out of 56

The snapshot reads `/proc/<pane_pid>/cwd` of the pane in the session's `amplifier` window.
muxplex reads tmux's own `#{pane_current_path}` for the session's active window's active
pane (`sessions.get_session_cwds()`; recorded onto each entry by
`manifest.update_manifest()` every poll cycle).

```
common sessions:        56
cwd disagreements:       0
```

Zero. `#{pane_current_path}` is tmux resolving the same kernel fact one layer higher —
`sessions.py`'s own docstring says so, and the measurement confirms it in practice on
every session that exists on this box.

Two honest caveats, neither of which changes the verdict:

- **Different pane.** The snapshot pins the `amplifier` window; muxplex follows the
  *active* window. For an `amplifier-workspace` session every window is rooted at the
  workspace dir, which is why they agree today. A session whose active window has been
  `cd`'d elsewhere would diverge. `manifest.py` already documents this exact limitation
  and `restore.py` already treats a recorded cwd as *evidence to refuse on*, never as
  *evidence to act on* — so a divergent read produces a refusal, not a wrong restore.
- **Different cadence.** muxplex samples every ~2s; the snapshot every 5 min. The
  manifest is fresher by two orders of magnitude.

### 2.3 The `amplifier`-window requirement is the snapshot's only unique fact

The snapshot's `tmux list-panes -t "$s:amplifier"` is not just a filter — it is *positive
evidence that the session is a declaratively-rebuildable amplifier workspace*. muxplex has
no equivalent signal, which is why it refuses a session at `/tmp/...` that the snapshot
would happily rebuild.

That is the entire capability delta, and today it is **one session**:

```
manifest cwd != ~/dev/<name>   (i.e. muxplex would refuse these):   5
    attention-manager           -> /home/bkrabach                                  (not in snapshot)
    cos-gateway                 -> /home/bkrabach/dev/.../voice-chief-of-staff     (not in snapshot)
    relay-probe-15dc            -> /home/bkrabach/dev/.../lane-demo                (not in snapshot)
    vcos-review                 -> /home/bkrabach/dev/.../voice-chief-of-staff     (not in snapshot)
    dependabot-security-review  -> /tmp/dependabot-security-review                 ← IN SNAPSHOT
```

55 of the snapshot's 56 entries are exactly `~/dev/<name>`, which is verbatim what
muxplex's reserved default pair builds (`new_session_template` on this host is
`amplifier-workspace ~/dev/{name}` — not the shipped `tmux new-session -d -s {name}`
default). For those 55, `muxplex restore` and `amplifier-workspace-restore` do the *same
thing*.

**The one exception evaporates on inspection.** `/tmp` on this host is cleared at boot:

```
/usr/lib/tmpfiles.d/tmp.conf:   D /tmp 1777 root root 30d
systemd-tmpfiles-setup.service: ExecStart=systemd-tmpfiles --create --remove --boot ...
```

`D` + `--remove --boot` means `/tmp/dependabot-security-review` **will not exist after a
reboot**. `amplifier-workspace-restore` would log `FAIL missing dir: /tmp/...` and restore
nothing. So in the only scenario where boot-time restore would have run, the dotfiles
script cannot restore this session either. Its advantage over muxplex exists solely in a
*mid-uptime manual run* — a scenario in which a human is already sitting at the keyboard
and `muxplex restore` has just printed the directory in its refusal message.

**Nothing is lost. Not even the one exception.**

### 2.4 Trigger and dependency

| | dotfiles pair | muxplex |
|---|---|---|
| Record written | 5-min timer + boot | every ~2s poll, **only when structurally changed** |
| Record survives no-tmux-server | ❌ (guard is a threshold) | ✅ (`epoch_now is None` → return unchanged) |
| Restore trigger | `default.target` oneshot at boot | deliberate `muxplex restore` |
| Works with muxplex down | ✅ | ❌ (record goes stale) |
| Works with muxplex not installed | ✅ | ❌ |

The dependency question deserves a direct answer rather than a shrug:

**Yes, this creates a dependency on `muxplex.service` that did not exist. It is
acceptable, for three reasons.** (1) `muxplex.service` is already `WantedBy=default.target`
and is the owner's primary interface to these sessions — its being down is not a silent
condition, it is a blank dashboard noticed within minutes. (2) The alternative is not
"independence" but "a second record that destroys itself" — the dotfiles record's failure
mode is *most* active precisely when muxplex is down, because that is when nothing else is
watching. (3) The dependency is on the *record*, not on the *restore*: `sessions.json` is
plain JSON on local disk; if muxplex were uninstalled tomorrow the names, cwds, and
timestamps are still readable with `jq`. That is a strictly better fallback than a
clobbered `active` file.

The residual risk it introduces is stated and tracked in §9.

---

## 3. Why `muxplex restore` is sufficient — and better

### 3.1 It covers the same ground

For the 55 conventional workspaces: identical behavior, identical command
(`amplifier-workspace ~/dev/<name>`), sequential with per-name reporting, idempotent by
recomputing the plan against live tmux at execution time.

### 3.2 It has a fidelity check the dotfiles restore does not

`amplifier-workspace-restore` derives its target directory from the record and rebuilds
there. That is correct *because* the record only ever contained amplifier workspaces. Give
the same script a record containing `attention-manager -> /home/bkrabach` and it would run
`amplifier-workspace ~` — a catastrophic outcome the dotfiles pair avoids only by never
recording such a session.

muxplex's `_check_unrecorded_restore_fidelity()` (restore.py:127) handles it structurally:
a recorded cwd that diverges from `~/dev/<name>` is a **refusal with a reason**, and a
non-existent `~/dev/<name>` is a **hard floor** — restore never creates the directory.

### 3.3 A refusal carries more information than an omission

For `dependabot-security-review`, `muxplex restore` prints:

```
dependabot-security-review   FAIL  last observed running from '/tmp/dependabot-security-review',
    not '/home/bkrabach/dev/dependabot-security-review' -- restoring via the default session
    command would start the wrong process there. Restart it manually from its real location,
    and configure a session command pair ... so it can be restored faithfully next time.
```

That message contains *everything the snapshot record held about this session*, delivered
at the exact moment it is actionable, with the remedy named. The snapshot record would
have rebuilt it silently — which is better when it is right and worse when it is wrong,
and the record has no way to know which.

For sessions genuinely rooted off-convention that the owner *does* want rebuilt
automatically, the mechanism already exists and he already uses it: a `session_commands`
pair. `cos-gateway` in `~/.config/muxplex/settings.json` is the working exemplar.

### 3.4 A reboot and a `tmux kill-server` are the *same event* to the manifest

This is the load-bearing fact for both the design and the test plan.
`main.py:501` calls `load_manifest()` from disk on **every** poll cycle, then
`update_manifest(manifest, epoch_now, ...)`. The manifest holds no in-memory state across
cycles. So the discrimination is made purely from *(what is on disk)* × *(what the epoch
probe returns right now)* — and a reboot and a kill-server present identically:

- old epoch on disk, new/absent epoch probed → cold start
- no server at all → `epoch_now is None` → **return unchanged**, never tombstone

Consequence for testing: **post-reboot restore is fully testable without rebooting**
(§8, Gate G3). Consequence for operation: after a reboot the manifest is intact and
correct, and the cold start is frozen the moment the *first new tmux server arrives*.

---

## 4. Why the restore script must go too — the finding that settles it

The prompt allows keeping `amplifier-workspace-restore` and re-pointing it at
`sessions.json`. It should not be kept, and the reason is not redundancy. It is that
`amplifier-workspace-restore.service` is an instance of AGENTS.md mechanism #1 — *"restarting
a service whose cgroup has adopted the tmux server"* — sitting in the boot path.

`amplifier_workspace.tmux.create_session()` calls
`subprocess.run(["tmux", "new-session", ...])` with **no cgroup escape** (verified: no
`systemd-run`, no scope, nothing in the installed package). If no tmux server is running
when the unit executes — the normal state at boot — that `tmux new-session` **forks the
server as a child of the oneshot unit**, and every session it goes on to build lives in
the server inside `amplifier-workspace-restore.service`'s cgroup. `Type=oneshot` with no
`RemainAfterExit` means the unit deactivates the instant `ExecStart` returns, and the
default `KillMode=control-group` then SIGKILLs everything left in that cgroup.

**Canary, run on this host — per AGENTS.md, proven by behavior, not by reading the
directive back:**

```bash
# (a) Direct fork from a oneshot user unit — the dotfiles restore's shape
systemd-run --user --unit=probe --service-type=oneshot \
  /bin/sh -c "setsid sleep 400 >/dev/null 2>&1 </dev/null & exit 0"
# unit: inactive     surviving 'sleep 400' processes: 0
# → KILLED by cgroup teardown.   setsid did not help, exactly as cgroup_escape.py says.

# (b) systemd-run --user --scope from inside the same oneshot unit — muxplex's shape
systemd-run --user --unit=probe2 --service-type=oneshot \
  /bin/sh -c "{ systemd-run --user --scope --collect sleep 430 & }; sleep 3; exit 0"
# unit: inactive     /usr/bin/sleep 430 STILL RUNNING
# cgroup: 0::/user.slice/user-1000.slice/user@1000.service/app.slice/run-u102970.scope
# → SURVIVED. Escaped to app.slice, outside the unit entirely.
```

`muxplex restore` takes path (b): `sessions.spawn_session_command()` calls
`await should_escape()` and wraps via `wrap_shell_argv()` whenever `XDG_RUNTIME_DIR` is set
and `systemd-run` is on PATH — which is true inside any systemd user unit. **muxplex is
safe to invoke from a unit; the dotfiles script is not.**

Why this has never been seen: `uptime -s` = **2026-04-10**. The machine has not rebooted in
~4 months. `amplifier-workspace-restore.service`'s `ExecMainStartTimestamp` is
**2026-07-03**, a manual `systemctl --user start` while all sessions were already alive
(log: `total=61 restored=0 skipped=61`). The 45-session restore on 2026-08-05 was the
script run **by hand from an interactive shell** — path (b)'s equivalent, no unit, no
cgroup teardown. The unit has never once rebuilt a session at a real boot, and the script
itself documents that its interpreter resolution was broken before that anyway
(*"restore would have exited 2 on the first reboot that actually had sessions to
rebuild"*).

The boot-time auto-restore is therefore not a working capability that consolidation must
preserve. It is an untested code path that, on first execution, would rebuild ~57 sessions
over ~3 minutes and then have systemd kill the server holding all of them.

**And do not replace it with a boot unit that runs `muxplex restore`.** Even though (b)
proves that would survive cgroup teardown, it cannot work, for a reason upstream of
cgroups: at boot there is no tmux server, so `probe_tmux_epoch()` returns `None`,
`update_manifest()` returns unchanged, and `pending_restore` is never populated. A
boot-time `muxplex restore` would print `No cold start detected. Nothing to restore.` and
exit 0 — a silent no-op that *looks* like working automation. Making it work would require
the unit to first start a tmux server, then poll-wait for muxplex to observe the new epoch
and freeze the snapshot, then restore — a sequencing dance with a race at each step, built
to automate an event that has occurred zero times in four months. That is complexity
bought with the exact currency this consolidation exists to refund.

---

## 5. The honest ledger — what is actually lost

| Lost | Real cost |
|---|---|
| Automatic restore after reboot | **Nominal only.** Never executed successfully; verified to be self-destructive on first real execution (§4). Replaced by §6's procedure, rehearsed under G3. |
| Restore of an amplifier workspace rooted off `~/dev/<name>` | **One session today**, at `/tmp`, which will not exist after the only event that would trigger auto-restore (§2.3). Downgraded from silent rebuild to a named refusal that prints the directory. Permanent fix available and already in use: a `session_commands` pair. |
| A record that survives muxplex being down | Real, and tracked as residual risk R1 (§9). Traded for a record that survives tmux dying — which is the failure that has actually occurred, twice. |
| A second, independent record | This is the removal, not a cost. Two records that can disagree is the defect. |

| Gained | |
|---|---|
| One record, one writer, one removal rule | The `manifest.py` discipline applies to the whole system rather than half of it. |
| +4 sessions of coverage | The hand-started daemons, previously recorded nowhere. |
| ~150× fresher record | ~2s vs 5min. |
| Refusal instead of silent wrong restore | The 2026-08-05 failure mode is closed for every session, not just recorded ones. |
| A boot-time session destroyer removed | §4. |
| Two fewer scripts, three fewer units, one fewer state file | |

---

## 6. The replacement procedure (documentation, not code)

After any event that loses sessions — reboot, tmux server death, muxplex restart that
takes tmux with it:

```bash
muxplex restore --dry-run     # what would be rebuilt, and why anything is refused
muxplex restore               # rebuild (prompts; --yes to skip)
```

**After a reboot specifically, one ordering note:** the cold start is frozen when the
*first new tmux server arrives*, not at boot. Opening the muxplex dashboard and connecting
to anything, or running `tmux new-session -d -s scratch`, is sufficient; the next ~2s poll
freezes `pending_restore` and `muxplex restore` then has a plan. If `--dry-run` says
`No cold start detected`, no tmux server has come up yet — that is the whole diagnosis.

**No reminder mechanism is proposed, deliberately.** A workstation that comes back with 0
of 60 sessions and an empty muxplex dashboard is not a condition anyone fails to notice.
Adding a login hook to announce it would be code written to solve a problem that the
absence of 60 sessions already solves louder.

---

## 7. Considered on the muxplex side, and rejected

Recorded so these are not re-proposed as improvements.

1. **Record "has a window named `amplifier`"** so restore could act on a recorded cwd
   rather than refuse. Rejected: `amplifier` is application policy, and muxplex is a
   generic tmux multiplexer — this would put one specific workspace tool's window naming
   into the kernel of the presence record. It also buys exactly one session today, whose
   directory does not survive the triggering event.
2. **Restore at the recorded cwd** (`amplifier-workspace <observed_cwd>`) when
   `created_with` is `None`. Rejected, and it is the most dangerous of the options: it
   would run `amplifier-workspace ~` for `attention-manager`, reproducing the 2026-08-05
   incident with a bigger blast radius. A recorded cwd is evidence about *where*, never
   about *what* — `manifest.py` and `restore.py` both say so at length, and they are right.
3. **A boot-time `muxplex restore` unit.** Rejected in §4 — cannot work without a
   sequencing dance, and would fail silently while looking like it worked.
4. **Any `/api/*` addition.** None needed. `restore.py` is deliberately CLI-side with no
   HTTP dependency; nothing in this design touches the public contract, so the
   additive-only rule is satisfied vacuously.
5. **Keeping `amplifier-workspace-restore` re-pointed at `sessions.json`.** Rejected: it
   would be a second restore implementation, in bash+heredoc-python, without the fidelity
   check, invoked from the unit shape proven destructive in §4. Two restorers is the same
   class of defect as two records.

---

## 8. Verification — gates that must pass before this is trusted

The rule: **no deletion happens before the gate that justifies it passes.** Gates run in
order. G0–G2 are read-only on the live host. G3 runs in an isolated environment and never
touches the live tmux server or the live muxplex.

### G0 — Preconditions (live host, read-only)

```bash
systemctl --user is-active muxplex.service        # expect: active
test -s ~/.local/share/muxplex/sessions.json      # expect: exit 0
cp ~/.local/state/amplifier-workspace/active \
   ~/.local/state/amplifier-workspace/active.pre-consolidation-$(date +%s)
```

**Pass:** muxplex is running, the manifest is non-empty, the old record is archived.

### G1 — Equivalence proof (live host, read-only) — gates deletions 1–3

The measurement in §2, re-run at deletion time rather than trusted from this document.

```bash
python3 - <<'PY'
import json, pathlib
home = pathlib.Path.home()
man = json.loads((home/'.local/share/muxplex/sessions.json').read_text())
mcwd = {k: v.get('cwd') for k, v in man['sessions'].items()}
snap = dict(
    line.split('\t', 1)
    for line in (home/'.local/state/amplifier-workspace/active').read_text().splitlines()
    if line.strip()
)
only_snap = sorted(set(snap) - set(mcwd))
disagree  = [(n, mcwd[n], snap[n]) for n in set(mcwd) & set(snap) if mcwd[n] != snap[n]]
nocwd     = [n for n in snap if not mcwd.get(n)]
print("only in snapshot :", only_snap)
print("cwd disagreements:", disagree)
print("snapshot names with no manifest cwd:", nocwd)
print("GATE:", "PASS" if not (only_snap or disagree or nocwd) else "FAIL")
PY
```

**Pass criteria — all three must be empty:**
- `only in snapshot` — proves the manifest is a superset; a non-empty list means the
  manifest is missing a session the snapshot knows about, and deletion would lose data.
- `cwd disagreements` — proves the two cwd mechanisms resolve identically *right now*.
- `snapshot names with no manifest cwd` — proves restore-fidelity data is present for
  every session the old record covered.

**Fail → stop.** Do not delete anything; investigate the divergence first.

### G2 — Refusal inventory (live host, read-only) — informational, gates nothing

```bash
muxplex restore --dry-run
python3 - <<'PY'
import json, pathlib
home = pathlib.Path.home()
man = json.loads((home/'.local/share/muxplex/sessions.json').read_text())
for n, v in sorted(man['sessions'].items()):
    if v.get('cwd') != str(home/'dev'/n):
        print(f"WOULD REFUSE: {n:<30} {v.get('cwd')}")
PY
```

Produces the list of sessions that would need a `session_commands` pair or a manual
restart after a future loss. Today: 5. **The owner should read this list and decide
whether any of them warrant a pair** — that decision is his, and it is the only judgment
call this consolidation hands him.

### G3 — Cold-start rehearsal (isolated) — gates deletions 4–5

**This is the test that proves post-reboot restore works, without rebooting.** Its
validity rests on §3.4: the manifest reloads from disk every cycle and discriminates
purely on epoch identity, so a `tmux kill-server` and a reboot are the *same input*.

Run inside the repo's sanctioned isolated environment (`make test`'s DTU) or, at minimum,
under a scratch `HOME` — AGENTS.md: *"All config/state paths derive from `Path.home()` —
XDG env vars are ignored. Isolate scratch instances with a scratch HOME"*, plus
`env -u TMUX` and an isolated `TMUX_TMPDIR`, plus a monkeypatched `ttyd.TTYD_PORT`.

> **Never on the live host.** AGENTS.md: *"NEVER run the test suite on a host running a
> live muxplex."* `tmux kill-server` on `spark-1` reproduces the incident this spec exists
> to prevent.

Setup — deliberately including the shapes that must be refused:

| Fixture | Root | Expected restore outcome |
|---|---|---|
| `alpha`, `beta`, `gamma` | `$HOME/dev/<name>` | **OK** |
| `offroot` | `$HOME/dev/nested/deep/offroot` | **FAIL** — cwd divergence, message names the real dir |
| `homedaemon` | `$HOME` | **FAIL** — cwd divergence (this is `attention-manager`'s shape) |
| `ghost` | `$HOME/dev/ghost`, then `rm -rf` the dir | **FAIL** — hard floor; restore must not recreate the directory |

Steps and assertions:

1. Create the fixtures; wait for ≥2 poll cycles.
   **Assert:** all six appear in `sessions.json` with a `cwd`; `pending_restore` is `null`.
2. `tmux kill-server` on the scratch socket. Wait ≥2 cycles.
   **Assert:** manifest is **byte-identical** to step 1 — `epoch_now is None` must be a
   no-op. *This is the property the snapshot script lacked; assert it explicitly.*
3. `tmux new-session -d -s canary`. Wait ≥2 cycles.
   **Assert:** `pending_restore.sessions` == exactly the six fixtures (not `canary`), each
   carrying the `cwd` frozen from step 1; `epoch` is the new server.
4. `muxplex restore --dry-run`.
   **Assert:** plan lists the six, excludes `canary`, creates nothing.
5. `muxplex restore --yes`.
   **Assert:** `alpha`/`beta`/`gamma` → OK with >1 window; `offroot`/`homedaemon`/`ghost`
   → FAIL with the divergence or hard-floor message; **`$HOME/dev/ghost` was not
   recreated**; the three failures remain in `pending_restore` for retry.
6. Re-run `muxplex restore --yes`.
   **Assert:** idempotent — the three live ones are not duplicated, the three failures
   fail identically.
7. **Cgroup canary, in the isolated env:** run step 5 from inside a
   `systemd-run --user --service-type=oneshot` unit with no tmux server pre-existing.
   **Assert:** after the unit goes `inactive`, the restored sessions are **still alive**,
   and the tmux server's cgroup is `.../app.slice/run-*.scope` — i.e. the escape in
   `spawn_session_command()` held. Then run the same step with
   `amplifier-workspace-restore` in place of `muxplex restore` and **assert the opposite** —
   this is the direct, reproducible demonstration of §4 on the actual scripts rather than
   on `sleep`.

**Fail at any step → stop.** Deletions 4–5 are not justified.

### G4 — Post-consolidation soak (live host, 7 days)

After deletion, before removing the archived `active` file:

- Day 0: `muxplex restore --dry-run` still reports the same plan shape as G2.
- Day 1–7: `sessions.json`'s `epoch.observed_at` stays within a few minutes of now
  (cheap staleness check — proves the single remaining writer is writing).
- Day 7: no session has been lost without appearing in `pending_restore`.

**Pass → delete `active.pre-consolidation-*`.** Until then it is the rollback evidence.

---

## 9. Residual risks, stated rather than designed around

**R1 — The record is stale while `muxplex.service` is down.**
If muxplex is down and sessions are created or destroyed, `sessions.json` will not reflect
them; if tmux then dies, those sessions are unrecorded. Accepted because muxplex being
down is loud (blank dashboard), because the previous record's own failure mode was *worse*
in exactly that window, and because the check is one command:
`jq -r '.epoch.observed_at' ~/.local/share/muxplex/sessions.json`.
**Not mitigated with code.** A staleness warning is a new feature with a new failure mode,
proposed for a condition that has never occurred.

**R2 — Post-reboot restore now requires a human.**
Mitigated by §6 and by G3 proving the path works. Not mitigated by automation, for the
reasons in §4.

**R3 — 44 stale `pending_restore` entries are on disk right now**, all of them currently
live (verified: `pending NOT in sessions: []`). Harmless — `compute_restore_plan()`
subtracts live names, so the plan is empty, and the next real cold start replaces
`pending_restore` wholesale. Optional housekeeping **after G4 passes, not before**:
`muxplex restore --forget`. Doing it before G4 removes the record this consolidation is
being verified against.

---

## 10. Rollback

Every deletion is a tracked file in a git repo (`~/dotfiles`, all five confirmed under
`git ls-files`). Rollback is:

```bash
cd ~/dotfiles && git revert <commit>
systemctl --user daemon-reload
systemctl --user enable --now amplifier-workspace-snapshot.timer
cp ~/.local/state/amplifier-workspace/active.pre-consolidation-* \
   ~/.local/state/amplifier-workspace/active
```

The archived `active` file is what makes the state rollback real rather than nominal —
which is the same lesson the 3-day-old backup taught on 2026-08-05, applied on purpose
this time instead of by luck.

---

## 11. Execution order

1. G0, G1, G2 on the live host (read-only).
2. Delete 1–3 (`amplifier-workspace-snapshot` + `.service` + `.timer`);
   `systemctl --user disable --now amplifier-workspace-snapshot.timer`; `daemon-reload`.
3. G3 in the isolated environment.
4. Delete 4–5 (`amplifier-workspace-restore` + `.service`);
   `systemctl --user disable amplifier-workspace-restore.service`; `daemon-reload`.
5. Commit to `~/dotfiles` with the incident and the evidence in the message.
6. G4 soak; then remove the archive.

Steps 2 and 4 are separable on purpose: the snapshot record is unconditionally wrong and
can go as soon as G1 passes, independent of anything G3 finds about restore.

**No commits, PRs, or changes to the muxplex repository are required by this design.**

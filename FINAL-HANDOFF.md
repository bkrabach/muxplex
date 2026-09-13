# Maintainer handoff — muxplex maintenance

**Candidate:** `d61d2af08aad982ec5924717996202079e1c2cf3` on `lane/muxplex-maintenance`
**Base:** `a4c5aaf58124a418d2412d0118e4d23f95bbeb9b` (`v0.58.4`)
**State:** committed for independent manager review. Nothing was pushed, no PR was opened, and no external `DONE.json` was written.

## Item status

| Item | Status | Evidence |
|---|---|---|
| 1. Compose draft isolation | **PASS** | Drafts remain memory-only and device-qualified. Remote input is disabled rather than falling through to a same-named local session. Numeric remote ID `0` normalizes to `"0"` for state persistence, restore, and follow, so it remains remote and compose stays disabled. Each open/close owns a navigation generation; a stale B connection cannot mount, persist, or erase its saved draft after C wins. |
| 2. Diagnostics | **PASS** | The candidate retains the minimal PAM probe/actionable configured-PAM distinction and install-mode-aware upgrade recovery guidance. The complete Python suite covers the touched CLI/auth paths. |
| 3. Local CA guidance | **PASS** | The candidate retains generated CA paths, WSL PowerShell single-quote escaping, safe WSL input rejection, full browser restart wording, and client-side CA-file guidance rather than presenting a server configuration path as a remote-client path. |
| 4. Optional fonts | **BLOCKED / deferred** | No redistributable font binaries with verified provenance/notices plus real-browser metric proof were supplied. Per scope, no font feature or default-rendering change was added. |

## Final navigation/identity repair

- `muxplex/frontend/app.js`
  - `_normalizeRemoteId()` treats only `null`, `undefined`, and `""` as local. Numeric federation ID `0` becomes `"0"` before state persistence, restoration, following, compose fencing, and equality comparisons.
  - `_sessionNavigationGeneration` makes all post-`await` session-open effects supersedable. A connection that completes after a newer open or close returns without state PATCH, terminal mount, compose restore, or stale error cleanup.
  - `_composeTextareaOwnerKey` records which session owns the shared compose textarea. An interstitial B textarea is ownerless, so C cannot turn its empty value into deletion of B's saved draft. The fallback-local key rebases along with drafts when the actual local device ID becomes known.
- `muxplex/frontend/tests/test_app.mjs`
  - Exercises persisted numeric `0` through `restoreState()` and `followRemoteActiveSession()`: federation `/0/connect` is used, the local same-name route is absent, state carries `"0"`, and compose is disabled.
  - Exercises A → deferred B → C → B completion: A and B drafts survive correctly, only C mounts, and stale B never PATCHes active state.
  - Replaces two fixed-width source scans with complete function-body extraction, preserving the existing structural assertions when legitimate implementation growth occurs.

The preceding candidate repairs remain covered: synchronous STT retirement before abort; delayed-result/end identity gating; remote compose no-send; WSL PowerShell quote safety; and independently pending device-qualified sends.

## Full changed-file set since base

```text
FINAL-HANDOFF.md
docs/TRUSTING_THE_LOCAL_CA.md
muxplex/auth.py
muxplex/cli.py
muxplex/frontend/app.js
muxplex/frontend/tests/test_app.mjs
muxplex/frontend/tests/test_compose.mjs
muxplex/frontend/tests/test_stt.mjs
muxplex/setup_page.py
muxplex/tests/test_api.py
muxplex/tests/test_auth.py
muxplex/tests/test_cli.py
```

## Exact DTU verification

**DTU handed to:** manager session `dfdf46c8-cc44-4e45-afd1-c4ce71545d3b`
**DTU ID:** `muxplex-maintenance-dtu-20260913-r2`
**Limits:** 4 CPUs / 8 GiB
**Profile:** `.amplifier/digital-twin-universe/profiles/muxplex-maintenance-a4c5aaf.yaml`
**Access:** `http://localhost:38731/` and `http://muxplex-maintenance-dtu-20260913-r2.local:38731/`
**Teardown owner:** manager; `amplifier-digital-twin destroy muxplex-maintenance-dtu-20260913-r2`

The DTU was refreshed in place from a Git bundle advertising the lane branch and checked its clone SHA before swapping `/opt/muxplex`. Readiness passed after the final refresh, and installed-package provenance was:

```text
git_head=d61d2af08aad982ec5924717996202079e1c2cf3
git_tree=clean
muxplex_version=0.58.4
module_origin=/opt/muxplex/muxplex/__init__.py
dist_info=/opt/muxplex/.venv/lib/python3.12/site-packages/muxplex-0.58.4.dist-info
direct_url={"url":"file:///opt/muxplex","dir_info":{"editable":true}}
```

Passed inside that DTU after the final refresh:

```sh
cd /opt/muxplex/muxplex/frontend
node --test tests/*.mjs
# 1,408 passed, 0 failed, 0 skipped, 0 cancelled

cd /opt/muxplex
.venv/bin/python -m pytest muxplex/tests/
# 2,798 passed, 27 skipped, 56 deselected in 202.54s

.venv/bin/python -m ruff format --check muxplex/
# 98 files already formatted

.venv/bin/python -m ruff check muxplex/
# All checks passed

.venv/bin/python -m pyright muxplex/
# 0 errors, 0 warnings, 0 informations
```

For a focused rerun of the final navigation cases:

```sh
cd /opt/muxplex/muxplex/frontend
node --test --test-name-pattern='numeric remote id persists as "0"|follow with persisted numeric remote id|superseded slow B connection' tests/test_app.mjs
```

## Browser-harness route and residual

**Browser proof is not claimed.** The DTU has no browser executable or browser-automation package, and none was installed. No human-controlled browser session was used. A previous bridge attempt established only that the isolated app was reachable/authenticated; its first-run dialog could not safely be manipulated, so it is not evidence of compose behavior.

The controllable DOM/event-loop harness is `muxplex/frontend/tests/test_app.mjs`, run by the Node command above. It invokes the actual `app.js` open/restore/follow handlers with stateful DOM stubs and deferred network promises:

- `numeric remote id persists as "0" and restores through federation with compose disabled`
- `follow with persisted numeric remote id uses federation and keeps compose disabled`
- `superseded slow B connection preserves B draft and cannot mount or persist after C wins`
- `openSession A to B retires delayed A dictation before B starts`
- `openSession saves A before a failing B connect and rejects late A dictation`

For a manager-controlled managed-browser attempt, use the task-owned route `http://localhost:38731/` and authenticate through the DTU resource without recording credentials here. Real DOM/microphone-permission behavior and a live federated-peer card remain unproven; the deterministic Node harness is not presented as a browser result.

## Residual scope

- No production or host muxplex manipulation occurred.
- No packages, dependency pins, server restore/poll-cycle adoption, public API redesign, version bump, tag, publication, or font asset/default changed.
- No browser tooling was installed and no human-controlled browser session was used.
- The manager owns independent rerun, external lane marker creation, any PR/merge decision, and DTU teardown.
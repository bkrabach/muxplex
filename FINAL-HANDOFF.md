# Maintainer handoff — muxplex maintenance

**Candidate:** `826470472003b443fe6a311152017bc9b88dcf3a` on `lane/muxplex-maintenance`
**Base:** `a4c5aaf58124a418d2412d0118e4d23f95bbeb9b` (`v0.58.4`)
**State:** committed for independent manager review. Nothing was pushed, no PR was opened, and no external `DONE.json` was written.

## Item status

| Item | Status | Evidence |
|---|---|---|
| 1. Compose draft isolation | **PASS** | Drafts remain memory-only and device-qualified. Remote input is disabled rather than falling through to a same-named local session. Session handoff saves and clears A before B or an await. `_sttForceStop()` now retires A synchronously before `abort()`, and recognition callbacks are identity/generation-gated. Send deduplication is now a `Set` keyed by device-qualified session identity, allowing A/B overlap but denying a second send for either pending key. |
| 2. Diagnostics | **PASS** | The committed candidate retains the minimal PAM probe/actionable configured-PAM distinction and install-mode-aware upgrade recovery guidance. The complete Python suite covers the touched CLI/auth paths. |
| 3. Local CA guidance | **PASS** | The candidate retains generated CA paths, WSL PowerShell single-quote escaping, safe WSL input rejection, full browser restart wording, and client-side CA-file guidance rather than presenting a server configuration path as a remote-client path. |
| 4. Optional fonts | **BLOCKED / deferred** | No redistributable font binaries with verified provenance/notices plus real-browser metric proof were supplied. Per scope, no font feature or default-rendering change was added. |

## Final lifecycle repairs

- `muxplex/frontend/app.js`
  - `_sttForceStop()` stores the old recognizer locally, increments its generation, clears the singleton handle, and returns STT to `idle` **before** calling `abort()`. A delayed old `onresult` or `onend` cannot touch B, and B can start immediately.
  - `_composeSendInFlightKeys` replaces the single in-flight key. The current compose button is disabled only when its own device-qualified key is pending; `finally` deletes exactly the key created by that request.
- `muxplex/frontend/tests/test_stt.mjs`
  - Verifies recognizer retirement and idle state synchronously from inside `abort()`.
- `muxplex/frontend/tests/test_app.mjs`
  - Extends the real failing-connect A→B transition test to verify delayed `onend` is harmless.
  - Adds a real `openSession()` A→B transition: delayed A result/end leave B draft and B recognition untouched, then B ends normally without test-global masking.
- `muxplex/frontend/tests/test_compose.mjs`
  - Covers pending A → pending B → return A, including refusal of a duplicate A send and successful resend only after A's original request resolves.

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
**Teardown owner:** the manager; `amplifier-digital-twin destroy muxplex-maintenance-dtu-20260913-r2`

The DTU was refreshed in place from a verified Git bundle advertising exactly candidate `826470472003b443fe6a311152017bc9b88dcf3a` (bundle SHA-256 `592304120fe18c210c25c764262b97cb56c9813b31b37b5e53f6004799f09f46`). After all tests it proved:

```text
git_head=826470472003b443fe6a311152017bc9b88dcf3a
git_tree=clean
muxplex_version=0.58.4
module_origin=/opt/muxplex/muxplex/__init__.py
dist_info=/opt/muxplex/.venv/lib/python3.12/site-packages/muxplex-0.58.4.dist-info
direct_url={"dir_info":{"editable":true},"url":"file:///opt/muxplex"}
```

Passed inside that DTU:

```sh
cd /opt/muxplex/muxplex/frontend
node --test tests/test_compose.mjs tests/test_stt.mjs tests/test_app.mjs tests/test_shared_scope.mjs
# 749 passed, 0 failed, 0 skipped, 0 cancelled

node --test tests/*.mjs
# 1,405 passed, 0 failed, 0 skipped, 0 cancelled

cd /opt/muxplex
/opt/muxplex/.venv/bin/python -m pytest muxplex/tests/
# 2,798 passed, 27 skipped, 56 deselected in 198.98s

/opt/muxplex/.venv/bin/python -m ruff format --check muxplex/
# 98 files already formatted

/opt/muxplex/.venv/bin/python -m ruff check muxplex/
# All checks passed

/opt/muxplex/.venv/bin/python -m pyright muxplex/
# 0 errors, 0 warnings, 0 informations
```

Detailed update, readiness, provenance, and suite records are at `.amplifier/digital-twin-universe/profiles/test-results-8264704/`.

## Browser-proof route and residual

**Browser proof is not claimed.** This DTU has no browser executable or browser-automation package, and none was installed. A failed/unavailable browser bridge is not a pass.

The controllable DOM/event-loop harness the manager can rerun is the focused Node command above. It exercises the actual `app.js` handlers with stateful DOM stubs and deferred fetch/recognition callbacks:

- `test_compose.mjs`: `overlapping A and B sends remain independently pending`
- `test_stt.mjs`: `_sttForceStop retires recognition synchronously before .abort()`
- `test_app.mjs`: `openSession A to B retires delayed A dictation before B starts`

For a managed-browser attempt, the task-owned app route is `http://localhost:38731/`. If the bridge can safely reach it, authenticate through the manager-controlled DTU resource rather than recording a credential here. The current DTU has no federated peer fixture, so it cannot by itself demonstrate a real remote-session card; the Node harness is the supplied deterministic remote/identity race coverage. A real browser/federation proof remains the explicit manager residual.

## Residual scope

- No production or host muxplex manipulation occurred.
- No packages, dependency pins, restore flow, public API redesign, version bump, tag, or publication changed.
- No browser tooling was installed and no human-controlled browser session was used.
- No font assets/default change was added.

# Maintainer handoff — muxplex maintenance

**Ready for independent manager review.** Implementation was verified at `7e06fab67102a12a4dec03c20e464241c98d6e62` on `lane/muxplex-maintenance` (base: `a4c5aaf58124a418d2412d0118e4d23f95bbeb9b`); the immediately following commit records this handoff only. Nothing has been pushed and no PR was opened.

## Manager-review corrections

- Compose drafts remain in-memory and device-qualified with `buildSessionKey`. Session transition now saves and clears A before changing to B or awaiting connect; failed B cleanup cannot overwrite B's saved draft. Dictation callbacks capture identity/generation and ignore late A results.
- Remote compose, including follow-up queue attempts, is disabled with an explanation rather than being routed to a potentially same-named local session. The remote-keyed draft remains intact. Numeric federation id `0` is treated as remote too.
- WSL PowerShell CA paths escape embedded apostrophes by doubling them. The CA guide gives macOS/Linux clients a downloaded/copied `CA_FILE`, rather than presenting the server configuration path as a local client path. CLI direct-path instructions are explicitly scoped to the host where the command runs.

## Exact in-DTU proof

**DTU handed to manager:** `muxplex-maintenance-dtu-20260913-r2`
**Handoff owner:** manager session `dfdf46c8-cc44-4e45-afd1-c4ce71545d3b`
**Limits:** 4 CPUs / 8 GiB
**Access:** `http://localhost:38731/` (mDNS: `http://muxplex-maintenance-dtu-20260913-r2.local:38731/`)
**Profile:** `.amplifier/digital-twin-universe/profiles/muxplex-maintenance-a4c5aaf.yaml`
**Teardown when manager is finished:** `amplifier-digital-twin destroy muxplex-maintenance-dtu-20260913-r2`

The DTU was refreshed in place from a bundle advertising exactly `7e06fab67102a12a4dec03c20e464241c98d6e62`. Post-suite proof: checkout clean at that SHA; imported package `/opt/muxplex/muxplex/__init__.py`; editable `direct_url.json` is `file:///opt/muxplex`.

Successful commands inside the DTU:

```sh
cd /opt/muxplex && PATH=/opt/muxplex/.venv/bin:$PATH python -m pytest muxplex/tests/
# 2798 passed, 27 skipped, 56 deselected

cd /opt/muxplex/muxplex/frontend && node --test tests/*.mjs
# 1403 passed, 0 failed

cd /opt/muxplex && PATH=/opt/muxplex/.venv/bin:$PATH ruff format --check muxplex/
# 98 files already formatted

cd /opt/muxplex && PATH=/opt/muxplex/.venv/bin:$PATH ruff check muxplex/
# All checks passed

cd /opt/muxplex && PATH=/opt/muxplex/.venv/bin:$PATH pyright muxplex/
# 0 errors, 0 warnings, 0 informations
```

Focused regression pass before the complete run: 763 frontend tests, 289 `test_cli.py` tests, and the same static gates all passed. A first command using a stale frontend path exited before any test ran; it was corrected before the reported pass. The DTU update profile's stale activation script was bypassed with the explicit current venv `PATH`; this is a profile artifact, not a source-test failure.

## Residuals / scope

- No browser tooling is available in the DTU. No browser proof is claimed, and no browser package was installed as a workaround.
- Optional fonts remain deliberately deferred: no verified redistributable binary provenance plus real browser-metric proof was available.
- No production service, host muxplex, package version/pin, restore flow, or external resource manifest was changed in this iteration.

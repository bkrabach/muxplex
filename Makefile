# muxplex — developer targets
#
# `make test` runs the suite inside a Digital Twin Universe container rather
# than on your host. This is not optional hygiene: running pytest on a box that
# is also serving muxplex has destroyed a live settings.json and SIGTERMed the
# running server. See AGENTS.md -> "Testing & workflow".

DTU      ?= muxplex-test
PROFILE  ?= ../.amplifier/digital-twin-universe/profiles/muxplex-test.yaml
TARBALL  ?= ../.amplifier/digital-twin-universe/profiles/muxplex-src.tar.gz

.PHONY: test test-python test-frontend test-host check fmt check-container-drift

## Run BOTH suites inside the DTU (the safe, default path).
##
## THIS REPO HAS TWO SUITES. `make test` used to run only the Python one,
## and that gap shipped a red release candidate at v0.49.0: five commits
## whose largest surface was frontend JavaScript were verified against
## pytest alone, went green, and were pushed -- CI then failed with 31
## frontend failures (a stale test fixture and a set of tests still
## asserting a retired localStorage contract). Nothing was wrong with
## either suite. The gap was that only one of them was in anybody's loop,
## so the frontend suite was effectively opt-in and nobody opted in.
##
## Both now run here, and `test` depends on both, so the default local
## command covers the same ground CI does. Ordered frontend-first: it is
## ~15s against pytest's ~100s, so the fast suite reports before the slow
## one starts.
test: test-frontend test-python

## Frontend unit suite (node:test). Zero package dependencies, node:
## builtins only -- no install step, matching CI's own job. Runs in the DTU
## against the SAME git-archive snapshot as the Python suite, so both test
## the artifact you would push rather than your working tree.
test-frontend: dtu-sync
	@echo "==> frontend suite (node --test)"
	@amplifier-digital-twin exec $(DTU) -- bash -lc 'command -v node >/dev/null 2>&1 || { \
	  echo "node not found in the DTU -- the frontend suite CANNOT run."; \
	  echo "This is a FAILURE, not a skip: CI runs this suite and will fail"; \
	  echo "on what was never checked here. Install node in the DTU image."; \
	  exit 1; }; cd /opt/muxplex/muxplex/frontend && node --test tests/*.mjs'

## Python suite.
test-python: dtu-sync
	@echo "==> python suite (pytest)"
	@amplifier-digital-twin exec $(DTU) -- bash -lc 'cd /opt/muxplex && .venv/bin/pytest -q'

## Push HEAD into the DTU. Factored out so `make test` syncs ONCE and both
## suites run against the identical snapshot -- two separate syncs could
## otherwise test two different trees and report a green that never
## existed as one commit.
.PHONY: dtu-sync
dtu-sync:
	@command -v amplifier-digital-twin >/dev/null 2>&1 || { \
	  echo "amplifier-digital-twin not found."; \
	  echo "Install: uv tool install git+https://github.com/microsoft/amplifier-bundle-digital-twin-universe"; \
	  exit 1; }
	@git diff --quiet || echo "NOTE: uncommitted changes — commit first so the DTU tests what you'd push (AGENTS.md)."
	@echo "==> snapshotting HEAD"
	@git archive --format=tar.gz --prefix=muxplex/ -o "$(TARBALL)" HEAD
	@amplifier-digital-twin status $(DTU) >/dev/null 2>&1 || amplifier-digital-twin launch "$(PROFILE)" --name $(DTU)
	@amplifier-digital-twin file-push $(DTU) "$(TARBALL)" /root/muxplex-src.tar.gz >/dev/null
	@amplifier-digital-twin update $(DTU) >/dev/null

## Escape hatch: run on this host. Refuses if a live muxplex is serving.
test-host:
	@echo "Running on the HOST. The conftest guard will refuse if a live muxplex is up."
	uv run pytest

check: fmt
	@$(MAKE) --no-print-directory check-container-drift
	uv run ruff check muxplex/
	uv run pyright muxplex/

## Fail if the browser-verification container has drifted from this checkout.
##
## Browser proof is this project's reality gate, and it only means anything if
## the tree being clicked IS the tree being committed. That invariant decayed
## silently for 54 commits once (muxplex-cxd -> muxplex-cky) and was caught only
## because a person happened to look. This is the machine that looks instead.
##
## exit 1 (DRIFT) fails the build. exit 2 (could not verify -- no twin CLI, no
## container) is reported on screen but not fatal: a contributor without the LAN
## twin has no container to be stale. It is never silently treated as a pass.
check-container-drift:
	@./scripts/check_container_drift.py; rc=$$?; \
	  if [ $$rc -eq 1 ]; then exit 1; fi; \
	  if [ $$rc -eq 2 ]; then \
	    echo "[container-drift] NOT FATAL for 'make check' -- but this was NOT a pass."; \
	  fi; \
	  exit 0

fmt:
	uv run ruff format muxplex/

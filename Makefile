# muxplex — developer targets
#
# `make test` runs the suite inside a Digital Twin Universe container rather
# than on your host. This is not optional hygiene: running pytest on a box that
# is also serving muxplex has destroyed a live settings.json and SIGTERMed the
# running server. See AGENTS.md -> "Testing & workflow".

DTU      ?= muxplex-test
PROFILE  ?= ../.amplifier/digital-twin-universe/profiles/muxplex-test.yaml
TARBALL  ?= ../.amplifier/digital-twin-universe/profiles/muxplex-src.tar.gz

.PHONY: test test-host check fmt check-container-drift

## Run the full suite inside the DTU (the safe, default path).
test:
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
	@amplifier-digital-twin exec $(DTU) -- bash -lc 'cd /opt/muxplex && .venv/bin/pytest -q'

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

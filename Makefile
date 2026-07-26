# muxplex — developer targets
#
# `make test` runs the suite inside a Digital Twin Universe container rather
# than on your host. This is not optional hygiene: running pytest on a box that
# is also serving muxplex has destroyed a live settings.json and SIGTERMed the
# running server. See AGENTS.md -> "Testing & workflow".

DTU      ?= muxplex-test
PROFILE  ?= ../.amplifier/digital-twin-universe/profiles/muxplex-test.yaml
TARBALL  ?= ../.amplifier/digital-twin-universe/profiles/muxplex-src.tar.gz

.PHONY: test test-host check fmt

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
	uv run ruff check muxplex/
	uv run pyright muxplex/

fmt:
	uv run ruff format muxplex/

# muxplex — Conventions for Agents & Contributors

## The API is a public control surface, not a PWA backend

`/api/*` has consumers beyond the bundled frontend: the
[muxplex-deck](https://github.com/bkrabach/muxplex-deck) Stream Deck sidecar,
federation peers, and AI agents (the contract is discoverable at `/openapi.json`
and `/docs`; headless clients authenticate with the Bearer federation key).
Treat the API as a contract:

- **Prefer additive changes** (new fields, new endpoints). Renaming, removing, or
  changing the semantics of existing fields/endpoints breaks clients this repo's
  tests cannot see.
- **New capabilities land in the API first, frontend second** — never as
  frontend-only state or logic.
- Clients are expected to tolerate unknown fields; the server should tolerate
  their absence (version tolerance in both directions).

## Semantics external clients re-implement today (change with care)

These rules are currently ported into clients; silently changing them breaks
consumers in ways this repo's tests won't catch:

- **Needs-attention (bell) predicate**:
  `unseen_count > 0 and (seen_at is None or last_fired_at > seen_at)`
- **View membership entries** are normalized to `"device_id:name"` form by the
  background normalization pass; clients match by the `":<name>"` suffix
  (tmux forbids `:` in session names).
- **`last_activity_at`** derives from tmux `#{window_activity}` — deliberately
  NOT `#{session_activity}`, which freezes for unattended sessions (rationale
  and empirical evidence documented in `sessions.py`).
- **`active_view` / `active_session` are server-global** — last writer wins,
  across every connected client (browsers, deck, agents).

Preferred direction as semantics grow: move resolution **server-side** (e.g. a
resolved-current-view endpoint) rather than expecting each client to port more
logic — duplication across PWA/sidecar/agents is where drift bugs come from.

- **`GET /api/view`** is now the canonical server-side resolution of the
  above: view membership (via `filter_visible`), the needs-attention
  predicate (`bells.needs_attention`), and sort ordering (`?sort=attention`
  for tiered bell/active/recency ordering, or the default that mirrors
  `settings.sort_order`). New clients should prefer it over re-deriving
  these rules; local sessions only in v1.

## Running a second instance on one box (scratch/testing)

- All config/state paths derive from `Path.home()` — **XDG env vars are
  ignored**. Isolate scratch instances with a scratch `HOME`.
- **`TTYD_PORT` is hardcoded** (7682) and `kill_orphan_ttyd()` sweeps that port
  at startup — an unpatched second instance WILL kill the first instance's
  ttyd. Monkeypatch `muxplex.ttyd.TTYD_PORT` before importing `muxplex.main`.
- tmux isolation needs `env -u TMUX` plus an isolated `TMUX_TMPDIR` (a set
  `$TMUX` silently overrides `TMUX_TMPDIR`).
- Candidate future fixes: honor XDG paths; make the ttyd port configurable.

## Testing & workflow

- Python: `uv sync --extra dev && uv run pytest` (tests marked `integration`
  need a real tmux binary).
- Frontend: `node --test frontend/tests/test_app.mjs`.
- CI: `.github/workflows/ci.yml` (Python 3.11/3.12/3.13).
- PRs are squash-merged. `CHANGELOG.md` and version bumps happen at release
  time, by the owner — don't bump them in feature PRs.

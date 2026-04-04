# TLS Nudge in Doctor and Service Install — Design Addendum

## Goal

Surface TLS setup guidance to users at the right moments so they don't have to discover `muxplex setup-tls` on their own.

## Background

After installing muxplex and running `muxplex service install`, the service works on localhost — but clipboard doesn't work from other devices over plain HTTP. Users currently have to discover `muxplex setup-tls` on their own; there's no guidance pointing them to it. The `doctor` command already shows a TLS warning, but it doesn't tell users how to fix it.

## Approach

Contextual, non-blocking hints at the moments users are already looking at output. No wizard, no prompts, no blocking. Just a nudge with the exact command to run.

Three approaches were considered:

- **A. Nudge in `doctor` + `service install` (chosen)** — one-liner hints that show the fix command. Non-blocking; user decides when to act.
- **B. Interactive first-run wizard** — rejected; too heavy for a single optional command.
- **C. Auto-run `setup-tls` on `service install`** — rejected; TLS setup may need sudo or has visible effects (changes URL to HTTPS), so the user should opt in explicitly.

## Components

### 1. `doctor` output — enhanced TLS warning

**Current:**
```
  ! TLS: disabled (clipboard requires HTTPS on non-localhost)
```

**New:**
```
  ! TLS: disabled — clipboard won't work on remote devices
    Run: muxplex setup-tls
```

**Condition:** Only show when `host != 127.0.0.1` (network access) AND TLS is not configured. On localhost-only setups, TLS is unnecessary and the nudge is hidden.

### 2. `service install` output — tip after service start

After the "Service started" line and the URL, if `host != 127.0.0.1` and TLS is not configured, append:

```
  Tip: Enable HTTPS for clipboard support: muxplex setup-tls
```

One line, just a tip. Disappears once TLS is set up.

### 3. `upgrade` output — free via `doctor`

`upgrade` already calls `doctor` for its verification block, so the enhanced TLS warning shows there naturally. No extra code needed.

## Files to Modify

- `muxplex/cli.py` — `doctor()` function: update TLS warning text and add "Run:" hint line
- `muxplex/cli.py` — `service_install()` output: add tip line after "Service started"
- `muxplex/tests/test_cli.py` — tests for the nudge text appearing/not-appearing based on host and TLS config

## Testing Strategy

- Test that the nudge appears when `host=0.0.0.0` and TLS is not configured
- Test that the nudge does NOT appear when `host=127.0.0.1`
- Test that the nudge does NOT appear when TLS is already configured
- Test that `service install` includes the tip line under the right conditions

## Effort Estimate

~1 hour — small, well-scoped change to two output blocks.

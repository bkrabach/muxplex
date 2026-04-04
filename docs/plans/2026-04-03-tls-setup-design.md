# HTTPS/TLS Setup Design

## Goal

Add HTTPS support to muxplex so the browser Clipboard API works on non-localhost devices, via a `muxplex setup-tls` command with auto-detection that chooses the best available TLS method.

## Background

The browser Clipboard API (`navigator.clipboard`) requires a secure context — either `localhost` or HTTPS. When accessing muxplex from another device on the LAN (the common multi-device use case), clipboard operations silently fail because the connection is plain HTTP. This blocks copy/paste workflows that are central to terminal use.

Rather than requiring users to manually configure TLS certificates, muxplex should detect what's available on the system and set up the best option automatically.

## Approach

Auto-detection with tiered strategies. A single command — `muxplex setup-tls` — detects what's available and does the right thing:

1. **Tailscale** running + MagicDNS → `tailscale cert` (real Let's Encrypt cert, universally trusted)
2. **mkcert** installed → local CA cert (trusted on this machine, zero browser warnings)
3. **Fallback** → self-signed via Python (browser shows warning, but clipboard works)

No new Python package dependencies. `ssl` is stdlib, `subprocess` handles mkcert/tailscale CLI calls. uvicorn's built-in SSL support handles the serving side.

## Architecture

### Settings Integration

Two new keys in `settings.py` `DEFAULT_SETTINGS`:

| Key | Default | Description |
|-----|---------|-------------|
| `tls_cert` | `""` | Path to TLS certificate file. Empty = HTTP. |
| `tls_key` | `""` | Path to TLS private key file. Empty = HTTP. |

In `serve()`, if both are non-empty and files exist on disk, pass to `uvicorn.run(ssl_certfile=tls_cert, ssl_keyfile=tls_key)`. The server prints `https://` in its startup URL instead of `http://`.

If only one is set, or files don't exist at the configured paths, warn and fall back to HTTP.

### CLI Flags

`--tls-cert` and `--tls-key` flags on `muxplex serve`, using the same None-sentinel pattern as `--host` / `--port`. These override settings.json values.

`muxplex config set tls_cert /path/to/cert.pem` also works for persistent configuration.

### Detection Flow

```
muxplex setup-tls [--method=auto|tailscale|mkcert|selfsigned]

Default: --method=auto

1. Is Tailscale running + MagicDNS enabled?
   → YES: Use tailscale cert (real LE cert, universally trusted)
   → NO: continue

2. Is mkcert installed?
   → YES: Use mkcert (local CA, trusted on this machine)
   → NO: continue

3. Fallback: Generate self-signed via Python ssl module
   → Works but browser shows warning
   → Print: "For zero-warning HTTPS, install mkcert or use Tailscale"
```

---

## Components

### New Module: `muxplex/tls.py`

Contains all TLS-related logic: auto-detection, cert generation for each method, cert inspection (expiry, SANs), and status reporting.

### `muxplex setup-tls` Subcommand

**What it does:**

- Generates cert + key to `~/.config/muxplex/cert.pem` and `~/.config/muxplex/key.pem`
- Writes `tls_cert` and `tls_key` paths to `settings.json`
- Prints method used, hostname(s) in the cert, and expiry date
- Prints: "Restart service to apply: `muxplex service restart`"

**Tailscale variant:**

- Auto-detects MagicDNS hostname via `tailscale status --self --json`
- Runs `tailscale cert --cert-file ~/.config/muxplex/cert.pem --key-file ~/.config/muxplex/key.pem`
- Includes the local hostname as a reminder to access via `https://spark-1.tail8f3c4e.ts.net:8088`
- Notes the 90-day expiry and suggests: "Run `muxplex setup-tls` again to renew"

**mkcert variant:**

- Runs `mkcert -install` (may prompt for sudo/keychain access)
- Generates cert for: `$(hostname)`, `$(hostname).local`, `localhost`, `127.0.0.1`, `::1`
- If Tailscale is detected, also adds the Tailscale IP and MagicDNS name as SANs
- Prints instructions for trusting on other LAN devices: "Copy `$(mkcert -CAROOT)/rootCA.pem` to other devices"

**Self-signed fallback:**

- Uses Python `ssl` or `openssl` CLI to generate a basic self-signed cert
- Warns: "Browsers will show a security warning. Install mkcert for trusted certs."

### `muxplex setup-tls --status`

Shows current TLS state: method used, cert paths, expiry date, hostnames in the cert. Reuses the same inspection logic as `doctor`'s TLS section.

### Settings Changes (`muxplex/settings.py`)

Add `tls_cert` and `tls_key` to `DEFAULT_SETTINGS` with empty string defaults.

### Serve Changes (`muxplex/cli.py`)

- Add `--tls-cert` and `--tls-key` CLI flags to `serve` subcommand
- In `serve()`, resolve TLS paths (CLI flag → settings.json → empty)
- If both paths are non-empty and files exist, pass `ssl_certfile` and `ssl_keyfile` to `uvicorn.run()`
- Print `https://` URL on startup when TLS is active

### Doctor Integration

`muxplex doctor` shows TLS status:

- TLS enabled: `TLS: enabled (cert expires 2036-04-01)`
- TLS disabled: `TLS: disabled (clipboard requires HTTPS on non-localhost)`
- Cert expired: `TLS: WARNING — cert expired 5 days ago. Run muxplex setup-tls to renew`

---

## Service Integration

The service file (systemd/launchd) doesn't need to change — `serve()` already reads `tls_cert` and `tls_key` from `settings.json`. The service runs `muxplex serve` with zero extra flags.

**URL detection in `muxplex service install`:**

- TLS enabled: `Service started → https://spark-1.tail8f3c4e.ts.net:8088`
- TLS disabled: `Service started → http://0.0.0.0:8088`

**Tailscale cert renewal (90-day expiry):** `setup-tls` prints renewal instructions. No auto-cron job — users set that up themselves if wanted.

---

## Error Handling

### Detection Edge Cases

| Scenario | Behavior |
|----------|----------|
| Tailscale installed but not connected | Skip to mkcert detection |
| Tailscale connected but HTTPS certs not enabled | Print: "Enable HTTPS Certificates in your Tailscale admin console, then re-run" |
| mkcert installed but `mkcert -install` fails (no sudo, no certutil) | Warn and fall back to self-signed |
| Certs already exist from a previous run | Prompt: "TLS already configured (method: tailscale, expires 2026-07-03). Regenerate? [y/N]" |

### Runtime Edge Cases

| Scenario | Behavior |
|----------|----------|
| Cert files in settings but deleted from disk | Warn "TLS cert not found at path, falling back to HTTP" and start without SSL |
| Cert expired | `doctor` warns with expiry info and renewal instructions |
| Port conflict with HTTPS | Existing `_kill_stale_port_holder` behavior handles it |
| Only one of cert/key configured | Warn about incomplete TLS config, fall back to HTTP |

### WebSocket Considerations

- The frontend's `connectWebSocket()` already auto-detects `wss:` vs `ws:` from `location.protocol` — no change needed
- The federation proxy's `websockets.connect()` to remote instances already handles both `ws://` and `wss://` since `remote_instances[].url` can be either `http://` or `https://`

---

## Files to Modify

| File | Change |
|------|--------|
| `muxplex/settings.py` | Add `tls_cert`, `tls_key` to `DEFAULT_SETTINGS` |
| `muxplex/cli.py` | Add `setup-tls` subcommand, `--tls-cert`/`--tls-key` flags, SSL pass-through in `serve()` |
| `muxplex/tls.py` | **New module:** auto-detection, tailscale cert, mkcert, self-signed generation, cert inspection |
| `README.md` | Document `setup-tls` command and TLS configuration |

No changes needed to `muxplex/main.py` — uvicorn handles SSL transparently.

## Dependencies

- **No new Python package dependencies** — `ssl` is stdlib, `subprocess` for mkcert/tailscale CLI
- **mkcert** — optional external tool, detected at runtime
- **Tailscale** — optional external tool, detected at runtime
- **uvicorn SSL** — built-in support, already a dependency

## Testing Strategy

- Unit tests for detection logic (mock `shutil.which`, `subprocess.run` for each tool)
- Unit tests for cert inspection (expiry parsing, SAN extraction)
- Integration tests for `serve()` with SSL cert/key paths (valid and invalid)
- CLI tests for `setup-tls --status` output formatting
- Edge case tests: missing files, partial config, expired certs

## Effort Estimate

| Phase | Work | Time |
|-------|------|------|
| 1 | Settings + SSL serve | ~2 hours |
| 2 | `setup-tls` + auto-detection + all 3 methods | ~4 hours |
| 3 | Doctor integration + service URL detection | ~1 hour |
| 4 | README + tests | ~2 hours |
| **Total** | | **~9 hours** |

## Open Questions

None — all sections validated.

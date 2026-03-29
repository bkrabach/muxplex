# Authentication Design

## Goal

Add authentication to muxplex so that localhost access always bypasses auth (no friction for local use), non-localhost network access requires auth (prevents exposure on LAN/VPN), the default bind address changes from `0.0.0.0` to `127.0.0.1`, and each user runs their own muxplex instance with PAM naturally restricting to that user.

## Background

muxplex currently binds to `0.0.0.0` with no authentication. Anyone on the same network can access and control terminal sessions. This is fine for purely local use but dangerous on shared networks, VPNs, or cloud hosts. Authentication needs to be zero-friction for the common local case while making network exposure safe by default.

## Approach

PAM as the default auth backend (folded into main dependencies, not optional). Password mode as a fallback when PAM is unavailable. A custom login form with signed session cookies handles browser-based auth. The hard guard: binding to a non-localhost address without auth is impossible — PAM or an auto-generated password is always in place.

## Architecture

### Request Flow

All requests — including WebSocket upgrades — pass through a single FastAPI middleware:

```
Incoming request
  ├── client is 127.0.0.1 or ::1 → pass through (no auth check)
  ├── has valid session cookie → pass through
  ├── has Authorization: Basic header → validate (PAM or password), pass or 401
  └── none of the above → redirect to /login (or 401 for non-browser)
```

### Auth Mode Resolution (startup)

```
1. --auth=password or MUXPLEX_AUTH=password → password mode
2. try: import pam → success → PAM mode (default)
3. ImportError or PAM init failure → password mode + startup log line:
   "PAM unavailable, using password auth"
```

### PAM Mode

- Calls `pam.authenticate(username, password, service='login')`
- **Plus** a running-user check: `username == pwd.getpwuid(os.getuid()).pw_name`
- Both checks must pass — valid credentials for a *different* user are rejected
- Single-user isolation: the tmux socket is already per-user; the PAM check reinforces it

### Password Mode

Priority order for password resolution:

1. `MUXPLEX_PASSWORD` environment variable
2. `~/.config/muxplex/password` file (mode `0600`)
3. Neither set → auto-generate with `secrets.token_urlsafe(20)`, write to file, print at startup

### Login Form (`/login`)

- Standalone HTML page, no external JS dependencies (loads before auth)
- Styled with muxplex branding (dark theme, Urbanist wordmark, brand color tokens)
- **PAM mode:** username field (read-only, pre-filled with running user's name) + password field
- **Password mode:** single password field only
- `autocomplete="current-password"` (and `autocomplete="username"` in PAM mode)
- Password managers (1Password, Bitwarden, Safari Keychain) detect these attributes correctly

### Session Cookies

- Signed with `itsdangerous.TimestampSigner` using the server secret
- **Server secret:** `~/.config/muxplex/secret`, auto-generated on first run, mode `0600`
- Stateless — no server-side session store; verification is signature + expiry check
- Default expiry: 7 days (configurable via `--session-ttl` / `MUXPLEX_SESSION_TTL`)
- Attributes: `HttpOnly`, `SameSite=Strict`
- `/auth/logout` endpoint: clears cookie (`GET`, no auth required)

## Service Installation

### Platform Detection in `muxplex install-service`

#### Linux (systemd user service)

- Writes to `~/.config/systemd/user/muxplex.service`
- PAM mode (default): no credentials in unit file
- Password mode: `EnvironmentFile=~/.config/muxplex/env` (mode `0600`) — password never in unit file
- Default service binding: `--host 0.0.0.0` (network access is the point of a service)
- Prints activation commands but does not run them:
  ```bash
  systemctl --user daemon-reload
  systemctl --user enable --now muxplex
  # For always-on without login session:
  loginctl enable-linger $(whoami)
  ```
- `--linger` flag on `install-service` executes the `loginctl` call automatically

#### macOS (launchd agent)

- Writes to `~/Library/LaunchAgents/io.github.bkrabach.muxplex.plist`
- `RunAtLoad=true`, `KeepAlive=true` — starts at login, restarts on crash
- Prints: `launchctl load ~/Library/LaunchAgents/io.github.bkrabach.muxplex.plist`

#### Both Platforms

- `--port PORT` flag (default `8088`) for multi-user installations on same host
- Design note: `EnvironmentFile` for secrets is the correct systemd pattern — unit files can go in version control; env files never do

## Configuration Reference

### CLI Flags

| Flag | Default | Notes |
|------|---------|-------|
| `--host` | `127.0.0.1` | Changed from previous `0.0.0.0` default |
| `--port` | `8088` | Pick per-user on shared machines |
| `--auth` | `pam` | `pam` or `password` |
| `--session-ttl` | `7d` | `0` = session cookie (clears on browser close) |

### Environment Variables

| Variable | Maps to |
|----------|---------|
| `MUXPLEX_AUTH` | `--auth` |
| `MUXPLEX_PASSWORD` | Password for password mode (highest priority) |
| `MUXPLEX_SESSION_TTL` | `--session-ttl` |

### Config Files

All stored in `~/.config/muxplex/`, all mode `0600`:

| File | Purpose |
|------|---------|
| `password` | Auto-generated password (password mode only) |
| `secret` | Server signing key for session cookies |
| `env` | Sourced by systemd/launchd service unit |

### New Subcommands

- **`muxplex show-password`** — reads and prints `~/.config/muxplex/password` (or "using PAM" if PAM mode active)
- **`muxplex reset-secret`** — regenerates signing secret, invalidates all active sessions

## Data Flow

### Login (browser)

```
Browser → GET /login → login.html (with form)
Browser → POST /login (username?, password)
  Server → PAM: pam.authenticate(user, pass) + running-user check
        or Password: compare against resolved password
  Success → Set-Cookie (signed, HttpOnly, SameSite=Strict) → 302 /
  Failure → 401 → re-render login form with error
```

### Authenticated Request (browser)

```
Browser → GET /any-path (Cookie: session=<signed-token>)
  Middleware → verify signature + check expiry
  Valid → pass to route handler
  Invalid → redirect to /login
```

### Authenticated Request (API / CLI)

```
Client → GET /any-path (Authorization: Basic base64(user:pass))
  Middleware → validate credentials (PAM or password)
  Valid → pass to route handler
  Invalid → 401 Unauthorized
```

### Logout

```
Browser → GET /auth/logout
  Server → Set-Cookie: session=; Max-Age=0 → 302 /login
```

## Error Handling

- **PAM import failure at startup:** fall back to password mode, log one line: `"PAM unavailable, using password auth"`
- **PAM authentication failure:** 401 with generic message (never reveal whether username or password was wrong)
- **Tampered cookie:** treated as no cookie — redirect to `/login` for browsers, 401 for API clients
- **Expired cookie:** same as tampered — redirect or 401
- **Missing `~/.config/muxplex/` directory:** auto-create with mode `0700`
- **Secret file missing:** auto-generate on first run
- **Password file unreadable (permissions):** startup error with clear message about expected file permissions

## Testing Strategy

### Middleware Unit Tests (all auth mocked)

Seven request shapes covering every middleware branch:

1. Localhost (`127.0.0.1` / `::1`) → passes through, no auth check
2. Valid session cookie + non-localhost → passes through
3. Expired or tampered cookie → redirects to `/login`
4. No cookie, non-localhost → redirects to `/login`
5. `Authorization: Basic` header, valid credentials → passes through
6. `Authorization: Basic` header, invalid credentials → 401
7. WebSocket upgrade with valid cookie → passes (same middleware path)

### Login Form Tests

- `GET /login` → correct HTML: PAM mode has read-only username field pre-filled with running user; password mode has single password field
- `POST /login`, PAM mode, correct user + password → cookie set, redirect to `/`
- `POST /login`, PAM mode, wrong password → 401
- `POST /login`, PAM mode, different username (valid creds for another user) → 401
- `POST /login`, password mode, correct password → cookie set, redirect
- `POST /login`, password mode, wrong password → 401

### PAM Mocking Strategy

- **Mock target:** `pam.authenticate()` from `python-pam` — not `libpam.so`, not the route handler
- **ImportError path:** PAM unavailable → falls back to password mode, emits one startup log line
- Principle: don't test the PAM library — test your code that calls it. One mock, at the right layer.

### Password and Secret Management

- Auto-generate: creates `~/.config/muxplex/password` with mode `0600`
- `show-password`: reads and prints correct file (mocked path in tests)
- `reset-secret`: writes a new signing key; cookies signed with old key fail verification
- `MUXPLEX_PASSWORD` env var takes precedence over file in all tests

### Session Cookie Tests

- Sign → verify round trip works
- Tampered cookie (modified payload) → rejected
- Timestamp-expired cookie → rejected
- Correct `HttpOnly` and `SameSite=Strict` attributes set

### Service File Tests

- Extend existing `test_cli.py` patterns
- Linux: unit file content correct, `EnvironmentFile=` present when password mode, absent when PAM
- macOS: plist content correct, `RunAtLoad` and `KeepAlive` set

## Implementation Notes

Key files to create or modify:

| File | Changes |
|------|---------|
| `muxplex/auth.py` | **New module.** Auth mode resolution, PAM wrapper, password resolution, cookie signing/verification, middleware class |
| `muxplex/main.py` | Add auth middleware to FastAPI app, `/login` route (GET + POST), `/auth/logout` route |
| `muxplex/cli.py` | New `--auth` and `--session-ttl` flags, `show-password` and `reset-secret` subcommands, update `install-service` for auth-aware unit files |
| `muxplex/frontend/login.html` | **New file.** Standalone login page with dark theme, muxplex branding, conditional username/password fields |

## Dependencies Added

| Package | Version | Purpose |
|---------|---------|---------|
| `python-pam` | `>=1.8.4` | PAM authentication (folded into main deps) |
| `itsdangerous` | `>=2.1.0` | `TimestampSigner` for session cookies |

## Open Questions

1. **macOS PAM quirks.** `python-pam` on older macOS has known issues. Document the PAM fallback behavior clearly in the README so users know what to expect if PAM fails silently.
2. **`reset-secret` confirmation.** Should `muxplex reset-secret` warn the user that all active sessions will be invalidated? Recommendation: yes, print a warning before writing the new secret.
3. **`--session-ttl 0` semantics.** `0` means session cookie (clears on browser close). Consider whether this needs a separate `--session-cookie` flag for clarity, or if `0` is intuitive enough.

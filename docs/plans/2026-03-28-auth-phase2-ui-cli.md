# Auth Phase 2: Login UI + CLI — Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Build the branded login page, complete login/logout routes, and add all CLI auth commands (`--auth`, `--session-ttl`, `show-password`, `reset-secret`, startup logging).

**Architecture:** Phase 1 established `auth.py` (middleware, password/secret/cookie/PAM functions) and a stub `/login` route. Phase 2 replaces the stub with a fully branded `login.html` that auto-detects PAM vs password mode, adds the POST `/login` and GET `/auth/logout` handlers, and wires the CLI flags and subcommands that control auth behavior at startup.

**Tech Stack:** Python 3.11+, FastAPI, HTML/CSS/JS (no framework), argparse, pytest

**Phase:** 2 of 2 — complete Phase 1 (`2026-03-28-auth-phase1-infrastructure.md`) before starting this phase.

**Design doc:** `docs/plans/2026-03-28-auth-design.md`

**Prerequisite:** Phase 1 must be complete. Verify: `python -m pytest muxplex/tests/ -v` — all tests pass, `muxplex/auth.py` exists with `AuthMiddleware`, `/login` stub and `/auth/mode` endpoint exist in `main.py`.

---

### Task 1: Create branded login.html

**Files:**
- Create: `muxplex/frontend/login.html`
- Modify: `muxplex/tests/test_frontend_html.py` (add login.html tests)

**Step 1: Write the failing tests**

Append to `muxplex/tests/test_frontend_html.py`:

```python
# ---------------------------------------------------------------------------
# login.html tests
# ---------------------------------------------------------------------------

_LOGIN_HTML_PATH = pathlib.Path(__file__).parent.parent / "frontend" / "login.html"


def _login_soup() -> BeautifulSoup:
    """Parse login.html — separate from index.html soup."""
    return BeautifulSoup(_LOGIN_HTML_PATH.read_text(), "html.parser")


def test_login_html_exists() -> None:
    """login.html must exist in the frontend directory."""
    assert _LOGIN_HTML_PATH.exists(), f"Missing {_LOGIN_HTML_PATH}"


def test_login_html_has_form() -> None:
    """login.html must contain a POST form targeting /login."""
    soup = _login_soup()
    form = soup.find("form")
    assert form is not None, "Missing <form> element"
    assert form.get("method", "").lower() == "post", "Form method should be POST"
    assert form.get("action") == "/login", "Form action should be /login"


def test_login_html_has_password_autocomplete() -> None:
    """login.html password field must have autocomplete='current-password'."""
    soup = _login_soup()
    pw_input = soup.find("input", attrs={"autocomplete": "current-password"})
    assert pw_input is not None, "Missing password input with autocomplete='current-password'"


def test_login_html_has_wordmark() -> None:
    """login.html must include the muxplex wordmark SVG."""
    soup = _login_soup()
    # Check for either an <img> with wordmark or inline SVG
    img = soup.find("img", attrs={"src": lambda s: s and "wordmark" in s})
    assert img is not None, "Missing muxplex wordmark image"


def test_login_html_references_muxplex_auth() -> None:
    """login.html must reference window.MUXPLEX_AUTH for mode detection."""
    text = _LOGIN_HTML_PATH.read_text()
    assert "MUXPLEX_AUTH" in text, "login.html must reference MUXPLEX_AUTH for mode detection"


def test_login_html_has_error_display() -> None:
    """login.html must have an element for displaying auth errors."""
    soup = _login_soup()
    # Look for an element that handles error state
    text = _LOGIN_HTML_PATH.read_text()
    assert "error" in text.lower(), "login.html must handle error display (query param ?error=1)"
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_frontend_html.py -v -k "login" 2>&1 | head -20`
Expected: FAIL — `login.html` doesn't exist yet

**Step 3: Create the branded login.html**

Create `muxplex/frontend/login.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta name="theme-color" content="#0D1117" />
  <title>muxplex — login</title>
  <link rel="icon" type="image/x-icon" href="/favicon.ico" />
  <link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png" />
  <style>
    /* Inline styles — login page must render before any auth */
    *,*::before,*::after{box-sizing:border-box}
    :root {
      --bg: #0D1117;
      --bg-surface: #1A1F2B;
      --text: #F0F6FF;
      --text-muted: #8E95A3;
      --border: #2A3040;
      --border-subtle: #1E2430;
      --accent: #00D9F5;
      --accent-hover: #00b8d1;
      --err: #f85149;
      --font-ui: system-ui, -apple-system, 'Segoe UI', sans-serif;
    }
    html,body {
      height: 100%; margin: 0; padding: 0;
      background: var(--bg); color: var(--text);
      font-family: var(--font-ui); font-size: 14px;
    }
    .login-wrapper {
      min-height: 100vh; display: flex;
      align-items: center; justify-content: center;
      padding: 24px;
    }
    .login-card {
      width: 100%; max-width: 380px;
      background: var(--bg-surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 40px 32px 32px;
    }
    .login-wordmark {
      display: block; margin: 0 auto 32px;
      height: 28px;
    }
    .login-field { margin-bottom: 16px; }
    .login-label {
      display: block; font-size: 13px;
      color: var(--text-muted); margin-bottom: 6px;
    }
    .login-input {
      width: 100%; padding: 10px 12px;
      background: var(--bg); color: var(--text);
      border: 1px solid var(--border-subtle);
      border-radius: 6px; font-size: 14px;
      font-family: var(--font-ui);
      outline: none; transition: border-color 150ms ease;
    }
    .login-input:focus {
      border-color: var(--accent);
    }
    .login-input[readonly] {
      opacity: 0.6; cursor: not-allowed;
    }
    .login-btn {
      width: 100%; padding: 10px 0; margin-top: 8px;
      background: var(--accent); color: var(--bg);
      border: none; border-radius: 6px;
      font-size: 14px; font-weight: 600;
      font-family: var(--font-ui);
      cursor: pointer; transition: background 150ms ease;
    }
    .login-btn:hover { background: var(--accent-hover); }
    .login-error {
      background: rgba(248,81,73,0.1);
      border: 1px solid var(--err);
      color: var(--err); border-radius: 6px;
      padding: 10px 12px; margin-bottom: 16px;
      font-size: 13px; display: none;
    }
    .login-error.visible { display: block; }
    #username-field { display: none; }
  </style>
</head>
<body>
  <div class="login-wrapper">
    <div class="login-card">
      <img src="/wordmark-on-dark.svg" alt="muxplex" class="login-wordmark" />

      <div id="login-error" class="login-error">
        Invalid credentials. Please try again.
      </div>

      <form method="post" action="/login">
        <div id="username-field" class="login-field">
          <label class="login-label" for="username">Username</label>
          <input id="username" name="username" type="text"
                 class="login-input" autocomplete="username" readonly />
        </div>

        <div class="login-field">
          <label class="login-label" for="password">Password</label>
          <input id="password" name="password" type="password"
                 class="login-input" autocomplete="current-password"
                 placeholder="Enter password" autofocus />
        </div>

        <button type="submit" class="login-btn">Sign in</button>
      </form>
    </div>
  </div>

  <script>
    // Auth mode is injected by the server as window.MUXPLEX_AUTH = {mode, user}
    (function() {
      var auth = window.MUXPLEX_AUTH || {mode: 'password', user: ''};

      // Show username field in PAM mode
      if (auth.mode === 'pam' && auth.user) {
        var field = document.getElementById('username-field');
        var input = document.getElementById('username');
        field.style.display = 'block';
        input.value = auth.user;
      }

      // Show error if redirected back with ?error=1
      if (window.location.search.indexOf('error=1') !== -1) {
        document.getElementById('login-error').classList.add('visible');
      }
    })();
  </script>
</body>
</html>
```

**Step 4: Run tests to verify they pass**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_frontend_html.py -v -k "login"`
Expected: all 6 login tests PASS

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add muxplex/frontend/login.html muxplex/tests/test_frontend_html.py && git commit -m "feat: branded login.html with PAM/password mode detection"
```

---

### Task 2: POST /login handler

**Files:**
- Modify: `muxplex/main.py`
- Modify: `muxplex/tests/test_api.py`

**Step 1: Write the failing tests**

Append to `muxplex/tests/test_api.py`:

```python
# ---------------------------------------------------------------------------
# POST /login
# ---------------------------------------------------------------------------


def test_post_login_correct_password_redirects_to_root(client, monkeypatch):
    """POST /login with correct password returns 303 redirect to / with session cookie."""
    monkeypatch.setattr("muxplex.main._auth_mode", "password")
    monkeypatch.setattr("muxplex.main._auth_password", "test-pw")

    response = client.post(
        "/login",
        data={"password": "test-pw"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "muxplex_session" in response.cookies


def test_post_login_wrong_password_redirects_to_login_error(client, monkeypatch):
    """POST /login with wrong password returns 303 redirect to /login?error=1."""
    monkeypatch.setattr("muxplex.main._auth_mode", "password")
    monkeypatch.setattr("muxplex.main._auth_password", "test-pw")

    response = client.post(
        "/login",
        data={"password": "wrong-pw"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "/login" in response.headers["location"]
    assert "error=1" in response.headers["location"]


def test_post_login_pam_mode_correct_creds(client, monkeypatch):
    """POST /login in PAM mode with correct creds sets cookie and redirects."""
    monkeypatch.setattr("muxplex.main._auth_mode", "pam")
    monkeypatch.setattr(
        "muxplex.auth.authenticate_pam",
        lambda u, p: True,
    )

    response = client.post(
        "/login",
        data={"username": "testuser", "password": "correct"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "muxplex_session" in response.cookies


def test_post_login_pam_mode_wrong_creds(client, monkeypatch):
    """POST /login in PAM mode with wrong creds redirects to /login?error=1."""
    monkeypatch.setattr("muxplex.main._auth_mode", "pam")
    monkeypatch.setattr(
        "muxplex.auth.authenticate_pam",
        lambda u, p: False,
    )

    response = client.post(
        "/login",
        data={"username": "testuser", "password": "wrong"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=1" in response.headers["location"]
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_api.py -v -k "post_login" 2>&1 | head -20`
Expected: FAIL — 405 Method Not Allowed (no POST handler for `/login` yet)

**Step 3: Add the POST /login handler**

In `muxplex/main.py`, add `from fastapi import Request` to the existing FastAPI imports if not already present. Then add this route right after the existing `GET /login` route:

```python
@app.post("/login")
async def login_submit(request: Request):
    """Handle login form submission."""
    from muxplex.auth import authenticate_pam, create_session_cookie

    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")

    # Validate credentials
    if _auth_mode == "pam":
        ok = authenticate_pam(str(username), str(password))
    else:
        ok = str(password) == _auth_password

    if not ok:
        from starlette.responses import RedirectResponse

        return RedirectResponse("/login?error=1", status_code=303)

    # Success — set session cookie and redirect to /
    cookie = create_session_cookie(_auth_secret, _auth_ttl)
    from starlette.responses import RedirectResponse

    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        "muxplex_session",
        cookie,
        httponly=True,
        samesite="strict",
        max_age=_auth_ttl if _auth_ttl > 0 else None,
    )
    return response
```

Note: The `RedirectResponse` import may already be available from `starlette.responses` (used in `auth.py`). Use whatever import pattern is cleanest — either add to the top-level imports or keep the local imports. Prefer adding `from starlette.responses import RedirectResponse` to the module-level imports at the top.

**Step 4: Run tests to verify they pass**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_api.py -v -k "post_login"`
Expected: all 4 tests PASS

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add muxplex/main.py muxplex/tests/test_api.py && git commit -m "feat: POST /login handler for PAM and password modes"
```

---

### Task 3: GET /auth/logout

**Files:**
- Modify: `muxplex/main.py`
- Modify: `muxplex/tests/test_api.py`

**Step 1: Write the failing tests**

Append to `muxplex/tests/test_api.py`:

```python
# ---------------------------------------------------------------------------
# GET /auth/logout
# ---------------------------------------------------------------------------


def test_logout_redirects_to_login(client):
    """GET /auth/logout returns 303 redirect to /login."""
    response = client.get("/auth/logout", follow_redirects=False)
    assert response.status_code == 303
    assert "/login" in response.headers["location"]


def test_logout_clears_session_cookie(client):
    """GET /auth/logout deletes the muxplex_session cookie (max-age=0)."""
    response = client.get("/auth/logout", follow_redirects=False)
    # Check Set-Cookie header clears the cookie
    set_cookie = response.headers.get("set-cookie", "")
    assert "muxplex_session" in set_cookie
    # Cookie should be expired (max-age=0 or empty value)
    assert 'max-age=0' in set_cookie.lower() or '=""' in set_cookie or "=''" in set_cookie
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_api.py -v -k "logout" 2>&1 | head -20`
Expected: FAIL — 404 or 307 (no `/auth/logout` route yet)

**Step 3: Add the logout route**

In `muxplex/main.py`, add this route after the POST `/login` handler (and before the static file mount):

```python
@app.get("/auth/logout")
async def logout():
    """Clear the session cookie and redirect to login."""
    from starlette.responses import RedirectResponse

    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("muxplex_session")
    return response
```

**Step 4: Run tests to verify they pass**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_api.py -v -k "logout"`
Expected: both tests PASS

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add muxplex/main.py muxplex/tests/test_api.py && git commit -m "feat: GET /auth/logout clears session cookie"
```

---

### Task 4: Replace /login stub with branded login.html serving

**Files:**
- Modify: `muxplex/main.py`
- Modify: `muxplex/tests/test_api.py`

**Step 1: Write the failing test**

Append to `muxplex/tests/test_api.py`:

```python
# ---------------------------------------------------------------------------
# GET /login serves branded page with injected auth mode
# ---------------------------------------------------------------------------


def test_get_login_injects_muxplex_auth(client):
    """GET /login HTML must contain window.MUXPLEX_AUTH with the auth mode."""
    response = client.get("/login")
    assert response.status_code == 200
    assert "MUXPLEX_AUTH" in response.text
    assert '"mode"' in response.text
```

**Step 2: Run to verify it fails**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_api.py::test_get_login_injects_muxplex_auth -v`
Expected: FAIL — current stub doesn't have `MUXPLEX_AUTH`

**Step 3: Replace the GET /login handler**

In `muxplex/main.py`, replace the existing `login_page()` function with:

```python
@app.get("/login", response_class=HTMLResponse)
async def login_page():
    """Serve the branded login page with auth mode injected."""
    import json

    html = (_FRONTEND_DIR / "login.html").read_text()

    username = ""
    if _auth_mode == "pam":
        username = pwd.getpwuid(os.getuid()).pw_name

    mode_data = json.dumps({"mode": _auth_mode, "user": username})
    # Inject auth mode before </head> so the inline script can read it
    html = html.replace(
        "</head>",
        f"<script>window.MUXPLEX_AUTH = {mode_data};</script>\n</head>",
    )
    return HTMLResponse(html)
```

Note: `_FRONTEND_DIR` is already defined at the bottom of `main.py` as `pathlib.Path(__file__).parent / "frontend"`. It's used for the StaticFiles mount. You need to move this variable definition **above** the routes section so `login_page()` can reference it, or define it separately near the top. The simplest change: move the `_FRONTEND_DIR = pathlib.Path(__file__).parent / "frontend"` line to just after the imports/config section (around line 50), keeping the `app.mount(...)` line at the bottom.

**Step 4: Run to verify it passes**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_api.py -v -k "login"`
Expected: all login tests PASS (including the old `test_get_login_returns_200_html`)

**Step 5: Optionally remove the /auth/mode endpoint**

Since `login.html` now reads `window.MUXPLEX_AUTH` instead of fetching `/auth/mode`, the endpoint is redundant. However, it's harmless and could be useful for API clients. **Keep it** but it's no longer required for the login flow.

**Step 6: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add muxplex/main.py muxplex/tests/test_api.py && git commit -m "feat: /login GET serves branded login.html with injected auth mode"
```

---

### Task 5: CLI: update host default and add auth flags

**Files:**
- Modify: `muxplex/cli.py`
- Modify: `muxplex/tests/test_cli.py`

**Step 1: Write the failing tests**

Append to `muxplex/tests/test_cli.py`:

```python
# ---------------------------------------------------------------------------
# Auth CLI flags
# ---------------------------------------------------------------------------


def test_main_default_host_is_localhost():
    """Default --host must be 127.0.0.1 (changed from 0.0.0.0)."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex"]):
            main()
        call_kwargs = mock_serve.call_args
        assert call_kwargs[1]["host"] == "127.0.0.1" or call_kwargs[0][0] == "127.0.0.1"


def test_main_passes_auth_flag():
    """main() with --auth password must forward auth='password' to serve()."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex", "--auth", "password"]):
            main()
        _, kwargs = mock_serve.call_args
        assert kwargs.get("auth") == "password"


def test_main_passes_session_ttl_flag():
    """main() with --session-ttl 3600 must forward session_ttl=3600 to serve()."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex", "--session-ttl", "3600"]):
            main()
        _, kwargs = mock_serve.call_args
        assert kwargs.get("session_ttl") == 3600
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_cli.py -v -k "default_host or auth_flag or session_ttl" 2>&1 | head -20`
Expected: FAIL — default host is still `0.0.0.0`, no `--auth` or `--session-ttl` flags

**Step 3: Update cli.py**

In `muxplex/cli.py`, make these changes:

1. Change the `serve()` signature to accept auth params:

```python
def serve(host: str = "127.0.0.1", port: int = 8088, auth: str = "pam", session_ttl: int = 604800) -> None:
    """Start the muxplex server."""
    import uvicorn  # noqa: PLC0415

    os.environ.setdefault("MUXPLEX_PORT", str(port))
    if auth:
        os.environ.setdefault("MUXPLEX_AUTH", auth)
    os.environ.setdefault("MUXPLEX_SESSION_TTL", str(session_ttl))

    from muxplex.main import app  # noqa: PLC0415

    print(f"  muxplex → http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")
```

2. Change the `--host` default:

```python
    parser.add_argument(
        "--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)"
    )
```

3. Add new arguments after the `--port` argument:

```python
    parser.add_argument(
        "--auth",
        choices=["pam", "password"],
        default="pam",
        help="Auth mode: pam (default) or password",
    )
    parser.add_argument(
        "--session-ttl",
        type=int,
        default=604800,
        help="Session cookie TTL in seconds (default: 604800 = 7 days, 0 = browser session)",
    )
```

4. Update the `serve()` call in `main()` to pass the new args:

```python
    if args.command == "install-service":
        install_service(system=args.system)
    else:
        serve(host=args.host, port=args.port, auth=args.auth, session_ttl=args.session_ttl)
```

**Step 4: Update the existing test that checks the old default**

The existing test `test_main_calls_serve_by_default` asserts `host="0.0.0.0"`. Update it:

In `muxplex/tests/test_cli.py`, change:
```python
        mock_serve.assert_called_once_with(host="0.0.0.0", port=8088)
```
to:
```python
        mock_serve.assert_called_once_with(host="127.0.0.1", port=8088, auth="pam", session_ttl=604800)
```

**Step 5: Run tests to verify they pass**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_cli.py -v`
Expected: all tests PASS (including updated existing tests)

**Step 6: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add muxplex/cli.py muxplex/tests/test_cli.py && git commit -m "feat(cli): add --auth and --session-ttl flags, change --host default to 127.0.0.1"
```

---

### Task 6: CLI: show-password subcommand

**Files:**
- Modify: `muxplex/cli.py`
- Modify: `muxplex/tests/test_cli.py`

**Step 1: Write the failing tests**

Append to `muxplex/tests/test_cli.py`:

```python
# ---------------------------------------------------------------------------
# show-password subcommand
# ---------------------------------------------------------------------------


def test_show_password_prints_password_from_file(tmp_path, monkeypatch, capsys):
    """show-password prints the password when the file exists."""
    from muxplex.cli import main

    # Set up a fake password file
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    pw_path = fake_home / ".config" / "muxplex" / "password"
    pw_path.parent.mkdir(parents=True, exist_ok=True)
    pw_path.write_text("my-test-password\n")
    pw_path.chmod(0o600)

    # Force password mode
    monkeypatch.setenv("MUXPLEX_AUTH", "password")

    with patch("sys.argv", ["muxplex", "show-password"]):
        main()

    captured = capsys.readouterr()
    assert "my-test-password" in captured.out


def test_show_password_no_file(tmp_path, monkeypatch, capsys):
    """show-password prints a helpful message when no password file exists."""
    from muxplex.cli import main

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    monkeypatch.setenv("MUXPLEX_AUTH", "password")

    with patch("sys.argv", ["muxplex", "show-password"]):
        main()

    captured = capsys.readouterr()
    assert "no password" in captured.out.lower() or "not found" in captured.out.lower()


def test_show_password_pam_mode(monkeypatch, capsys):
    """show-password in PAM mode prints that PAM is active."""
    from muxplex.cli import main

    monkeypatch.delenv("MUXPLEX_AUTH", raising=False)
    monkeypatch.setattr("muxplex.auth.pam_available", lambda: True)

    with patch("sys.argv", ["muxplex", "show-password"]):
        main()

    captured = capsys.readouterr()
    assert "pam" in captured.out.lower()
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_cli.py -v -k "show_password" 2>&1 | head -20`
Expected: FAIL — `show-password` subcommand doesn't exist

**Step 3: Add the show-password subcommand**

In `muxplex/cli.py`, add the function:

```python
def show_password() -> None:
    """Show the current muxplex password."""
    from muxplex.auth import load_password, pam_available

    auth_mode = os.environ.get("MUXPLEX_AUTH", "").lower()
    if auth_mode != "password" and pam_available():
        print("Auth mode: PAM — no password file used")
        return

    pw = load_password()
    if pw:
        print(f"Password: {pw}")
    else:
        print("No password file found. Start muxplex to auto-generate one.")
```

Then register it as a subcommand in `main()`. Add after the `install-service` subparser:

```python
    sub.add_parser("show-password", help="Show the current muxplex password")
```

And in the command dispatch:

```python
    if args.command == "install-service":
        install_service(system=args.system)
    elif args.command == "show-password":
        show_password()
    else:
        serve(host=args.host, port=args.port, auth=args.auth, session_ttl=args.session_ttl)
```

**Step 4: Run tests to verify they pass**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_cli.py -v -k "show_password"`
Expected: all 3 tests PASS

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add muxplex/cli.py muxplex/tests/test_cli.py && git commit -m "feat(cli): add show-password subcommand"
```

---

### Task 7: CLI: reset-secret subcommand

**Files:**
- Modify: `muxplex/cli.py`
- Modify: `muxplex/tests/test_cli.py`

**Step 1: Write the failing tests**

Append to `muxplex/tests/test_cli.py`:

```python
# ---------------------------------------------------------------------------
# reset-secret subcommand
# ---------------------------------------------------------------------------


def test_reset_secret_writes_new_secret(tmp_path, monkeypatch, capsys):
    """reset-secret writes a new secret file."""
    from muxplex.cli import main

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    with patch("sys.argv", ["muxplex", "reset-secret"]):
        main()

    secret_path = fake_home / ".config" / "muxplex" / "secret"
    assert secret_path.exists()
    content = secret_path.read_text().strip()
    assert len(content) > 20


def test_reset_secret_sets_0600_permissions(tmp_path, monkeypatch, capsys):
    """reset-secret sets the secret file to mode 0600."""
    from muxplex.cli import main

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    with patch("sys.argv", ["muxplex", "reset-secret"]):
        main()

    secret_path = fake_home / ".config" / "muxplex" / "secret"
    mode = stat.S_IMODE(secret_path.stat().st_mode)
    assert mode == 0o600


def test_reset_secret_prints_warning(tmp_path, monkeypatch, capsys):
    """reset-secret prints a warning about invalidated sessions."""
    from muxplex.cli import main

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    with patch("sys.argv", ["muxplex", "reset-secret"]):
        main()

    captured = capsys.readouterr()
    assert "invalid" in captured.out.lower() or "warning" in captured.out.lower()
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_cli.py -v -k "reset_secret" 2>&1 | head -20`
Expected: FAIL — `reset-secret` subcommand doesn't exist

**Step 3: Add the reset-secret subcommand**

In `muxplex/cli.py`, add the function:

```python
def reset_secret() -> None:
    """Regenerate the signing secret, invalidating all active sessions."""
    import secrets as _secrets

    from muxplex.auth import get_secret_path

    path = get_secret_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    new_secret = _secrets.token_urlsafe(32)
    path.write_text(new_secret + "\n")
    path.chmod(0o600)
    print(f"New signing secret written to {path}")
    print("Warning: all active sessions are now invalid.")
```

Register it as a subcommand. Add after the `show-password` subparser:

```python
    sub.add_parser("reset-secret", help="Regenerate signing secret (invalidates sessions)")
```

And in the command dispatch:

```python
    if args.command == "install-service":
        install_service(system=args.system)
    elif args.command == "show-password":
        show_password()
    elif args.command == "reset-secret":
        reset_secret()
    else:
        serve(host=args.host, port=args.port, auth=args.auth, session_ttl=args.session_ttl)
```

Also add `import stat` to the test file imports if not already present.

**Step 4: Run tests to verify they pass**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_cli.py -v -k "reset_secret"`
Expected: all 3 tests PASS

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add muxplex/cli.py muxplex/tests/test_cli.py && git commit -m "feat(cli): add reset-secret subcommand"
```

---

### Task 8: Startup auth logging

**Files:**
- Modify: `muxplex/main.py` (refine `_resolve_auth` logging)
- Modify: `muxplex/tests/test_auth.py`

**Step 1: Write the failing tests**

Append to `muxplex/tests/test_auth.py`:

```python
# ---------------------------------------------------------------------------
# Startup auth logging (via _resolve_auth)
# ---------------------------------------------------------------------------


def test_resolve_auth_pam_mode_logs_pam(monkeypatch, capsys, tmp_path):
    """_resolve_auth() prints PAM auth line when PAM is available."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.delenv("MUXPLEX_AUTH", raising=False)
    monkeypatch.delenv("MUXPLEX_PASSWORD", raising=False)
    monkeypatch.setattr("muxplex.auth.pam_available", lambda: True)

    # Import after patching
    from muxplex.main import _resolve_auth

    mode, pw = _resolve_auth()
    assert mode == "pam"
    captured = capsys.readouterr()
    assert "PAM" in captured.err


def test_resolve_auth_env_password_logs_env(monkeypatch, capsys, tmp_path):
    """_resolve_auth() prints env password line when MUXPLEX_PASSWORD is set."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setenv("MUXPLEX_AUTH", "password")
    monkeypatch.setenv("MUXPLEX_PASSWORD", "from-env")

    from muxplex.main import _resolve_auth

    mode, pw = _resolve_auth()
    assert mode == "password"
    assert pw == "from-env"
    captured = capsys.readouterr()
    assert "env" in captured.err.lower()


def test_resolve_auth_file_password_logs_file(monkeypatch, capsys, tmp_path):
    """_resolve_auth() prints file password line when password file exists."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setenv("MUXPLEX_AUTH", "password")
    monkeypatch.delenv("MUXPLEX_PASSWORD", raising=False)

    pw_path = tmp_path / ".config" / "muxplex" / "password"
    pw_path.parent.mkdir(parents=True, exist_ok=True)
    pw_path.write_text("file-password\n")
    pw_path.chmod(0o600)

    from muxplex.main import _resolve_auth

    mode, pw = _resolve_auth()
    assert mode == "password"
    assert pw == "file-password"
    captured = capsys.readouterr()
    assert "file" in captured.err.lower() or "password" in captured.err.lower()


def test_resolve_auth_generates_password_as_last_resort(monkeypatch, capsys, tmp_path):
    """_resolve_auth() auto-generates a password when nothing else is available."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setenv("MUXPLEX_AUTH", "password")
    monkeypatch.delenv("MUXPLEX_PASSWORD", raising=False)

    from muxplex.main import _resolve_auth

    mode, pw = _resolve_auth()
    assert mode == "password"
    assert len(pw) > 10
    captured = capsys.readouterr()
    assert "generated" in captured.err.lower()
    # The generated password should be printed so the user can see it
    assert pw in captured.err
```

**Step 2: Run tests to verify they fail or pass**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_auth.py -v -k "resolve_auth" 2>&1 | head -30`
Expected: These tests verify the `_resolve_auth` function added in Phase 1 Task 7. They may pass already if the logging was correctly implemented. If they fail, fix the `_resolve_auth` function.

**Step 3: Refine _resolve_auth logging if needed**

Verify the `_resolve_auth()` function in `muxplex/main.py` prints exactly these lines to stderr:

- PAM available: `muxplex auth: PAM (user: {username})`
- Env password: `muxplex auth: password (env)`  
- File password: `muxplex auth: password (file: ~/.config/muxplex/password)`
- Auto-generated: `muxplex auth: password generated — {password} — saved to ~/.config/muxplex/password`

Update the function if the format doesn't match. Fix the `file_pw` logging line — the Phase 1 plan had a bug (it printed `load_password.__module__` instead of the file path). It should be:

```python
    file_pw = load_password()
    if file_pw:
        from muxplex.auth import get_password_path
        print(f"  muxplex auth: password (file: {get_password_path()})", file=sys.stderr)
        return "password", file_pw
```

**Step 4: Run all tests**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_auth.py -v -k "resolve_auth"`
Expected: all 4 tests PASS

**Step 5: Run the full test suite**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/ -v`
Expected: ALL tests pass across all test files

**Step 6: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add muxplex/main.py muxplex/tests/test_auth.py && git commit -m "feat: startup auth mode logging with auto-generated password display"
```

---

## Phase 2 Complete Checklist

After all 8 tasks:

- [ ] `muxplex/frontend/login.html` exists — branded dark theme, wordmark, PAM/password mode detection
- [ ] POST `/login` works — correct creds → cookie + redirect to `/`, wrong creds → redirect to `/login?error=1`
- [ ] GET `/auth/logout` works — clears cookie, redirects to `/login`
- [ ] GET `/login` serves `login.html` with `window.MUXPLEX_AUTH` injected
- [ ] `--host` defaults to `127.0.0.1`
- [ ] `--auth` and `--session-ttl` flags work
- [ ] `muxplex show-password` prints the password or PAM message
- [ ] `muxplex reset-secret` regenerates the signing key with warning
- [ ] Startup prints one clear auth mode line to stderr
- [ ] All tests pass: `python -m pytest muxplex/tests/ -v`
- [ ] 8 clean commits with conventional commit messages

## End-to-End Smoke Test

After both phases are complete, manually verify:

1. `cd /home/bkrabach/dev/web-tmux/muxplex && python -m muxplex --host 0.0.0.0` — should print auth mode line
2. Open `http://localhost:8088` — should load the dashboard (localhost bypass)
3. Open from another device on the LAN — should redirect to `/login`
4. Log in with the displayed password — should redirect to dashboard
5. `muxplex show-password` — prints the password
6. `muxplex reset-secret` — prints warning, old browser session should fail

## Deferred

- HTTPS/TLS support
- Rate limiting on login endpoint
- Remember-me longer TTL
- Admin reset flow
- `install-service` auth-aware unit files (systemd EnvironmentFile, launchd plist)
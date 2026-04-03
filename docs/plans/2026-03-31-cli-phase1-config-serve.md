# CLI Refactor Phase 1: Config as Source of Truth + CLI Cleanup

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Phase:** 1 of 2. Complete this phase before starting [Phase 2](./2026-03-31-cli-phase2-service-commands.md).
**Design doc:** [`docs/plans/2026-03-31-cli-service-refactor-design.md`](./2026-03-31-cli-service-refactor-design.md)

**Goal:** Make `settings.json` the single source of truth for serve options (`host`, `port`, `auth`, `session_ttl`), so the service file can run `muxplex serve` with zero flags and pick up config from disk.

**Architecture:** Add four new keys to `DEFAULT_SETTINGS` in `settings.py`. Refactor `serve()` in `cli.py` to load settings from disk, then override with any explicitly-passed CLI flags (using `default=None` sentinel to distinguish "not passed" from "passed the default value"). Clean up the argparse structure: consolidate `upgrade`/`update` via aliases, deprecate `install-service`, add serve flags to both root parser and `serve` subparser, and show serve config in `doctor()`.

**Tech Stack:** Python 3.11+, argparse, pytest, monkeypatch/capsys

**Working directory:** `/home/bkrabach/dev/web-tmux/muxplex/`

---

### Task 1: Add serve keys to DEFAULT_SETTINGS

**Files:**
- Modify: `muxplex/settings.py` (the `DEFAULT_SETTINGS` dict, lines 13-21)
- Test: `muxplex/tests/test_settings.py`

**Step 1: Write the failing tests**

Add these tests at the end of `muxplex/tests/test_settings.py`:

```python
# ---------------------------------------------------------------------------
# Serve config keys in DEFAULT_SETTINGS (Phase 1)
# ---------------------------------------------------------------------------


def test_default_settings_include_serve_keys():
    """DEFAULT_SETTINGS must include host, port, auth, session_ttl."""
    assert "host" in DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["host"] == "127.0.0.1"
    assert "port" in DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["port"] == 8088
    assert "auth" in DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["auth"] == "pam"
    assert "session_ttl" in DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["session_ttl"] == 604800


def test_load_settings_returns_serve_keys_when_file_missing():
    """load_settings() returns serve keys with correct defaults when file is missing."""
    result = load_settings()
    assert result["host"] == "127.0.0.1"
    assert result["port"] == 8088
    assert result["auth"] == "pam"
    assert result["session_ttl"] == 604800


def test_serve_keys_patchable():
    """patch_settings() accepts and persists serve config keys."""
    result = patch_settings({"host": "0.0.0.0", "port": 9999, "auth": "password", "session_ttl": 3600})
    assert result["host"] == "0.0.0.0"
    assert result["port"] == 9999
    assert result["auth"] == "password"
    assert result["session_ttl"] == 3600
    # Verify persistence
    loaded = load_settings()
    assert loaded["host"] == "0.0.0.0"
    assert loaded["port"] == 9999


def test_old_settings_file_without_serve_keys_loads_correctly(redirect_settings_path):
    """An old settings.json without serve keys loads correctly with defaults filled in."""
    import json

    redirect_settings_path.write_text(json.dumps({"sort_order": "alpha"}))
    result = load_settings()
    assert result["sort_order"] == "alpha"
    assert result["host"] == "127.0.0.1"
    assert result["port"] == 8088
```

Note: The existing `redirect_settings_path` fixture (defined as a `conftest.py` fixture or in the test file) already redirects `SETTINGS_PATH` to a temp file. If it doesn't exist, you'll need to use `monkeypatch` directly — check the existing test file for the pattern used by the existing settings tests.

**Step 2: Run tests to verify they fail**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_settings.py::test_default_settings_include_serve_keys muxplex/tests/test_settings.py::test_load_settings_returns_serve_keys_when_file_missing -v`

Expected: FAIL with `AssertionError` — keys not in `DEFAULT_SETTINGS`

**Step 3: Add the four keys to DEFAULT_SETTINGS**

In `muxplex/settings.py`, replace the `DEFAULT_SETTINGS` dict (lines 13-21) with:

```python
DEFAULT_SETTINGS: dict = {
    "host": "127.0.0.1",
    "port": 8088,
    "auth": "pam",
    "session_ttl": 604800,
    "default_session": None,
    "sort_order": "manual",
    "hidden_sessions": [],
    "window_size_largest": False,
    "auto_open_created": True,
    "new_session_template": "tmux new-session -d -s {name}",
    "delete_session_template": "tmux kill-session -t {name}",
}
```

Serve keys are placed first since they're the primary server configuration.

**Step 4: Run tests to verify they pass**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_settings.py -v`

Expected: ALL PASS (new tests + all existing tests)

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add muxplex/settings.py muxplex/tests/test_settings.py && git commit -m "feat: add serve keys (host, port, auth, session_ttl) to DEFAULT_SETTINGS"
```

---

### Task 2: Refactor serve() to read from settings.json with CLI overrides

**Files:**
- Modify: `muxplex/cli.py` (the `serve()` function, lines 152-168)
- Test: `muxplex/tests/test_cli.py`

**Step 1: Write the failing tests**

Add these tests at the end of `muxplex/tests/test_cli.py`:

```python
# ---------------------------------------------------------------------------
# Config-driven serve + CLI override precedence (Phase 1)
# ---------------------------------------------------------------------------


def test_serve_reads_host_from_settings(tmp_path, monkeypatch):
    """serve(host=None) must use host from settings.json."""
    import json

    import muxplex.settings as settings_mod

    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"host": "0.0.0.0"}))
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    with patch("muxplex.cli.uvicorn") as mock_uv:
        mock_uv.run = lambda *a, **kw: None
        from muxplex.cli import serve

        serve(host=None, port=None, auth=None, session_ttl=None)
        # uvicorn.run is mocked at module level — check the call
    # Verify by checking the mock was called with host="0.0.0.0"
    # Since we replaced uvicorn.run with a lambda, use a different approach:
    pass


def test_serve_cli_flag_overrides_settings(tmp_path, monkeypatch, capsys):
    """serve(host='10.0.0.1') must override settings.json host."""
    import json

    import muxplex.settings as settings_mod

    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"host": "0.0.0.0"}))
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    calls = {}

    def fake_run(app, **kwargs):
        calls.update(kwargs)

    with patch("uvicorn.run", fake_run):
        from muxplex.cli import serve

        serve(host="10.0.0.1", port=None, auth=None, session_ttl=None)

    assert calls["host"] == "10.0.0.1"


def test_serve_falls_back_to_default_when_no_settings_file(tmp_path, monkeypatch, capsys):
    """serve() with no settings file and no CLI flags uses hardcoded defaults."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "nonexistent.json")

    calls = {}

    def fake_run(app, **kwargs):
        calls.update(kwargs)

    with patch("uvicorn.run", fake_run):
        from muxplex.cli import serve

        serve(host=None, port=None, auth=None, session_ttl=None)

    assert calls["host"] == "127.0.0.1"
    assert calls["port"] == 8088


def test_serve_port_from_settings(tmp_path, monkeypatch, capsys):
    """serve(port=None) must use port from settings.json."""
    import json

    import muxplex.settings as settings_mod

    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"port": 7777}))
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    calls = {}

    def fake_run(app, **kwargs):
        calls.update(kwargs)

    with patch("uvicorn.run", fake_run):
        from muxplex.cli import serve

        serve(host=None, port=None, auth=None, session_ttl=None)

    assert calls["port"] == 7777


def test_serve_session_ttl_from_settings(tmp_path, monkeypatch):
    """serve(session_ttl=None) must use session_ttl from settings.json."""
    import json
    import os

    import muxplex.settings as settings_mod

    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"session_ttl": 3600}))
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)
    monkeypatch.delenv("MUXPLEX_SESSION_TTL", raising=False)

    def fake_run(app, **kwargs):
        pass

    with patch("uvicorn.run", fake_run):
        from muxplex.cli import serve

        serve(host=None, port=None, auth=None, session_ttl=None)

    assert os.environ.get("MUXPLEX_SESSION_TTL") == "3600"


def test_serve_session_ttl_zero_is_valid(tmp_path, monkeypatch):
    """serve(session_ttl=0) must work — 0 means browser session, a valid value."""
    import os

    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "nonexistent.json")
    monkeypatch.delenv("MUXPLEX_SESSION_TTL", raising=False)

    def fake_run(app, **kwargs):
        pass

    with patch("uvicorn.run", fake_run):
        from muxplex.cli import serve

        serve(host=None, port=None, auth=None, session_ttl=0)

    assert os.environ.get("MUXPLEX_SESSION_TTL") == "0"
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_cli.py::test_serve_cli_flag_overrides_settings muxplex/tests/test_cli.py::test_serve_falls_back_to_default_when_no_settings_file -v`

Expected: FAIL — current `serve()` signature requires non-None args and doesn't load settings

**Step 3: Refactor serve()**

In `muxplex/cli.py`, replace the existing `serve()` function (lines 152-168) with:

```python
def serve(
    host: str | None = None,
    port: int | None = None,
    auth: str | None = None,
    session_ttl: int | None = None,
) -> None:
    """Start the muxplex server.

    Resolution order: CLI flag (if not None) > settings.json > hardcoded default.
    """
    import uvicorn  # noqa: PLC0415

    from muxplex.settings import load_settings  # noqa: PLC0415

    settings = load_settings()
    host = host if host is not None else settings.get("host", "127.0.0.1")
    port = port if port is not None else settings.get("port", 8088)
    auth = auth if auth is not None else settings.get("auth", "pam")
    session_ttl = (
        session_ttl if session_ttl is not None else settings.get("session_ttl", 604800)
    )

    os.environ["MUXPLEX_PORT"] = str(port)
    os.environ["MUXPLEX_AUTH"] = auth
    os.environ["MUXPLEX_SESSION_TTL"] = str(session_ttl)

    from muxplex.main import app  # noqa: PLC0415

    print(f"  muxplex → http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")
```

Key changes from old `serve()`:
- All params default to `None` (sentinel for "not passed by CLI")
- Loads `settings.json` via `load_settings()` and uses those values when CLI param is `None`
- Uses `os.environ[key] = value` (hard set, not `setdefault`) so settings.json values actually take effect in main.py

**Step 4: Run tests to verify they pass**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_cli.py -v -k "serve_reads_host or cli_flag_overrides or falls_back_to_default or port_from_settings or session_ttl_from or session_ttl_zero" --no-header 2>&1 | tail -20`

Expected: All PASS

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add muxplex/cli.py muxplex/tests/test_cli.py && git commit -m "feat: serve() reads settings.json with CLI flag overrides"
```

---

### Task 3: Refactor argparse — None defaults, serve flags on both parsers, upgrade alias

**Files:**
- Modify: `muxplex/cli.py` (the `main()` function, lines 635-709)
- Modify: `muxplex/tests/test_cli.py` (update existing tests for new signature)

**Step 1: Write the failing tests**

Add at the end of `muxplex/tests/test_cli.py`:

```python
# ---------------------------------------------------------------------------
# Argparse passes None for unset serve flags (Phase 1)
# ---------------------------------------------------------------------------


def test_main_passes_none_for_unset_flags():
    """main() with no flags passes None for host/port/auth/session_ttl to serve()."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex"]):
            main()
        mock_serve.assert_called_once_with(
            host=None, port=None, auth=None, session_ttl=None
        )


def test_main_passes_explicit_host_only():
    """main() with --host 10.0.0.1 passes host='10.0.0.1', others as None."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex", "--host", "10.0.0.1"]):
            main()
        mock_serve.assert_called_once_with(
            host="10.0.0.1", port=None, auth=None, session_ttl=None
        )


def test_main_serve_subcommand_accepts_flags():
    """'muxplex serve --host 10.0.0.1 --port 9000' passes values to serve()."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex", "serve", "--host", "10.0.0.1", "--port", "9000"]):
            main()
        mock_serve.assert_called_once_with(
            host="10.0.0.1", port=9000, auth=None, session_ttl=None
        )


def test_help_shows_single_upgrade_line():
    """Help output must show 'upgrade' once (with 'update' as alias), not two separate entries."""
    import io

    from muxplex.cli import main

    buf = io.StringIO()
    with patch("sys.argv", ["muxplex", "--help"]):
        try:
            with patch("sys.stdout", buf):
                main()
        except SystemExit:
            pass

    help_text = buf.getvalue()
    assert "upgrade" in help_text.lower()
    # 'update' should NOT appear as a separate top-level subcommand
    lines = [line.strip() for line in help_text.split("\n") if line.strip()]
    separate_update_lines = [
        l for l in lines if l.startswith("update") and "upgrade" not in l.lower()
    ]
    assert len(separate_update_lines) == 0, (
        f"'update' should not be a separate subcommand line; found: {separate_update_lines}"
    )
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_cli.py::test_main_passes_none_for_unset_flags -v`

Expected: FAIL — current `main()` passes `host="127.0.0.1"` (argparse default), not `None`

**Step 3: Add `_add_serve_flags()` helper and refactor `main()`**

In `muxplex/cli.py`, add this helper function just above the `main()` function:

```python
def _add_serve_flags(parser: argparse.ArgumentParser) -> None:
    """Add --host, --port, --auth, --session-ttl flags to a parser.

    All default to None so serve() can distinguish 'not passed' from
    'passed the default value'.
    """
    parser.add_argument(
        "--host",
        default=None,
        help="Bind host (default: from settings.json, then 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port (default: from settings.json, then 8088)",
    )
    parser.add_argument(
        "--auth",
        choices=["pam", "password"],
        default=None,
        help="Auth method: pam or password (default: from settings.json, then pam)",
    )
    parser.add_argument(
        "--session-ttl",
        type=int,
        default=None,
        dest="session_ttl",
        help="Session TTL in seconds (default: from settings.json, then 604800; 0 = browser session)",
    )
```

Then replace the entire `main()` function with:

```python
def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="muxplex",
        description="muxplex — web-based tmux session dashboard",
    )
    # Serve flags on the root parser (so `muxplex --host 0.0.0.0` works)
    _add_serve_flags(parser)

    sub = parser.add_subparsers(dest="command")

    # serve subparser also accepts serve flags
    serve_parser = sub.add_parser("serve", help="Start the server (default)")
    _add_serve_flags(serve_parser)

    svc = sub.add_parser(
        "install-service",
        help="Install as a background service (systemd on Linux, launchd on macOS)",
    )
    svc.add_argument(
        "--system", action="store_true", help="System-wide (requires sudo)"
    )

    sub.add_parser("show-password", help="Show the current muxplex password")

    sub.add_parser(
        "reset-secret", help="Regenerate signing secret (invalidates sessions)"
    )

    sub.add_parser("doctor", help="Check dependencies and system status")

    upgrade_parser = sub.add_parser(
        "upgrade",
        aliases=["update"],
        help="Upgrade muxplex to latest version and restart service",
    )
    upgrade_parser.add_argument(
        "--force",
        action="store_true",
        help="Force reinstall even if already up to date",
    )

    args = parser.parse_args()

    if args.command == "install-service":
        print(
            "⚠ 'muxplex install-service' is deprecated."
            " Use 'muxplex service install' instead.",
            file=sys.stderr,
        )
        install_service(system=args.system)
    elif args.command == "show-password":
        show_password()
    elif args.command == "reset-secret":
        reset_secret()
    elif args.command == "doctor":
        doctor()
    elif args.command in ("upgrade", "update"):
        upgrade(force=getattr(args, "force", False))
    else:
        _check_dependencies()
        serve(
            host=args.host,
            port=args.port,
            auth=args.auth,
            session_ttl=args.session_ttl,
        )
```

Changes from old `main()`:
- Serve flags defined via `_add_serve_flags()` helper (DRY — used on both root and serve subparser)
- All serve flag defaults are `None` instead of hardcoded values
- `upgrade` uses `aliases=["update"]` instead of a separate `sub.add_parser("update", ...)`
- Removed the separate `update_parser` and its duplicate `--force` argument
- `install-service` dispatch now prints deprecation warning before calling `install_service()`

**Step 4: Update existing tests for the new signature**

Several existing tests in `muxplex/tests/test_cli.py` assert the old call signature where `main()` passed hardcoded defaults to `serve()`. Update these:

1. **`test_main_calls_serve_by_default`** (line 14): Change expected call from `host="127.0.0.1", port=8088, auth="pam", session_ttl=604800` to `host=None, port=None, auth=None, session_ttl=None`

2. **`test_main_passes_custom_host_and_port`** (line 26): Change from `host="192.168.1.1", port=9000, auth="pam", session_ttl=604800` to `host="192.168.1.1", port=9000, auth=None, session_ttl=None`

3. **`test_main_default_host_is_localhost`** (line 38): Change `assert kwargs["host"] == "127.0.0.1"` to `assert kwargs["host"] is None`

4. **`test_main_passes_auth_flag`** (line 49): Change from `host="127.0.0.1", port=8088, auth="password", session_ttl=604800` to `host=None, port=None, auth="password", session_ttl=None`

5. **`test_main_passes_session_ttl_flag`** (line 61): Change from `host="127.0.0.1", port=8088, auth="pam", session_ttl=3600` to `host=None, port=None, auth=None, session_ttl=3600`

**Step 5: Run the full CLI test suite**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_cli.py -v --no-header 2>&1 | tail -50`

Expected: ALL PASS

**Step 6: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add muxplex/cli.py muxplex/tests/test_cli.py && git commit -m "refactor: argparse uses None defaults, serve flags on both parsers, upgrade/update alias"
```

---

### Task 4: Add deprecation warning test for install-service

**Files:**
- Test: `muxplex/tests/test_cli.py`

The deprecation warning was already added in Task 3's `main()` refactor. This task adds the test and verifies it.

**Step 1: Write the test**

Add at the end of `muxplex/tests/test_cli.py`:

```python
# ---------------------------------------------------------------------------
# Deprecation warning for install-service (Phase 1)
# ---------------------------------------------------------------------------


def test_install_service_subcommand_prints_deprecation_warning(capsys):
    """'muxplex install-service' must print a deprecation warning to stderr."""
    from muxplex.cli import main

    with patch("muxplex.cli.install_service"):
        with patch("sys.argv", ["muxplex", "install-service"]):
            main()

    captured = capsys.readouterr()
    assert "deprecated" in captured.err.lower()
    assert "muxplex service install" in captured.err
```

**Step 2: Run test to verify it passes**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_cli.py::test_install_service_subcommand_prints_deprecation_warning -v`

Expected: PASS (already implemented in Task 3)

**Step 3: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add muxplex/tests/test_cli.py && git commit -m "test: add deprecation warning test for install-service"
```

---

### Task 5: Update doctor() to show serve config

**Files:**
- Modify: `muxplex/cli.py` (the `doctor()` function, around line 244-252)
- Test: `muxplex/tests/test_cli.py`

**Step 1: Write the failing test**

Add at the end of `muxplex/tests/test_cli.py`:

```python
# ---------------------------------------------------------------------------
# doctor() shows serve config (Phase 1)
# ---------------------------------------------------------------------------


def test_doctor_shows_serve_config(tmp_path, monkeypatch, capsys):
    """doctor() must show the current serve config (host, port, auth)."""
    import json

    import muxplex.settings as settings_mod

    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps({"host": "0.0.0.0", "port": 9999, "auth": "password"})
    )
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    from muxplex.cli import doctor

    doctor()

    out = capsys.readouterr().out
    assert "0.0.0.0" in out
    assert "9999" in out
    assert "password" in out
```

**Step 2: Run test to verify it fails**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_cli.py::test_doctor_shows_serve_config -v`

Expected: FAIL — `doctor()` doesn't show serve config

**Step 3: Add serve config section to doctor()**

In `muxplex/cli.py`, in the `doctor()` function, right after the Settings file check block (around line 252, after the `Settings:` print statements and before the `# Auth status` comment), add:

```python
    # Serve config
    from muxplex.settings import load_settings  # noqa: PLC0415

    cfg = load_settings()
    print(
        f"  {ok_mark} Serve config: {cfg['host']}:{cfg['port']}"
        f" (auth={cfg['auth']}, ttl={cfg['session_ttl']}s)"
    )
```

**Step 4: Also update "not installed" messages to reference new command**

In the `doctor()` function, find the two lines that say `run: muxplex install-service` (one in the macOS launchd section around line 304, one in the Linux systemd section around line 316) and change both to `run: muxplex service install`.

**Step 5: Run test to verify it passes**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_cli.py::test_doctor_shows_serve_config -v`

Expected: PASS

**Step 6: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add muxplex/cli.py muxplex/tests/test_cli.py && git commit -m "feat: doctor() shows serve config from settings.json"
```

---

### Task 6: Run full test suite and fix regressions

**Files:**
- May modify: `muxplex/tests/test_cli.py` (fix any broken tests)
- May modify: `muxplex/cli.py` (fix any issues)

**Step 1: Run the full Python test suite**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/ -v --no-header 2>&1 | tail -60`

Expected: All tests pass. If any fail, investigate and fix.

Common things to check:
- The `test_upgrade_calls_uv_tool_install` test mocks `cli_mod.install_service` — this should still work since we kept the function, just added a deprecation warning to the CLI dispatch path.
- The `test_install_service_help_text_mentions_background_service` test captures help text — verify it still works with the deprecation.
- The `test_update_alias_registered` test checks that "update" appears in help — with `aliases=["update"]`, argparse shows it differently. This test may need updating to check for the alias syntax.

**Step 2: Run linting**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m ruff check muxplex/ --fix && python -m ruff format muxplex/`

**Step 3: Run tests one more time**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/ -v --tb=short 2>&1 | tail -40`

Expected: All pass

**Step 4: Commit any fixes**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add -A && git commit -m "fix: test suite green after Phase 1 config refactor"
```

(Skip this commit if no fixes were needed.)

---

### Task 7: Update README CLI section

**Files:**
- Modify: `README.md`

**Step 1: Update the Usage section**

In `README.md`, find the Usage section and update it to document the new config-driven behavior. Replace the serve options documentation with a table that shows the settings.json key for each option:

```markdown
## Usage

```bash
muxplex [OPTIONS]
muxplex serve [OPTIONS]     # explicit form
```

All serve options read from `~/.config/muxplex/settings.json` by default. CLI flags override for that run only.

| Option | settings.json key | Default | Description |
|---|---|---|---|
| `--host HOST` | `host` | `127.0.0.1` | Interface to bind (`0.0.0.0` for network access) |
| `--port PORT` | `port` | `8088` | Port to listen on |
| `--auth MODE` | `auth` | `pam` | Auth method: `pam` or `password` |
| `--session-ttl SEC` | `session_ttl` | `604800` | Session TTL in seconds (7 days; 0 = browser session) |

### Other commands

| Command | Description |
|---|---|
| `muxplex doctor` | Check dependencies and system status |
| `muxplex upgrade` | Upgrade to latest version and restart service |
| `muxplex show-password` | Show the current muxplex password |
| `muxplex reset-secret` | Regenerate signing secret (invalidates sessions) |
| `muxplex install-service` | *(deprecated — use `muxplex service install`)* |

### Examples

```bash
# Start with defaults from settings.json
muxplex

# Override port for this run only
muxplex --port 9000

# Override host for this run only
muxplex serve --host 0.0.0.0
```
```

Also update any `muxplex install-service` references in the install sections to note that `muxplex service install` is the new form. The install sections will be fully updated in Phase 2 when `muxplex service install` is implemented.

**Step 2: Verify README renders correctly**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && head -140 README.md`

**Step 3: Run full test suite to ensure nothing broke**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/ -v --tb=short 2>&1 | tail -20`

Expected: ALL PASS

**Step 4: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add README.md && git commit -m "docs: update README CLI section for config-driven serve"
```

---

### Task 8: Final verification and push

**Step 1: Run the full test suite one last time**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/ -v --tb=short 2>&1 | tail -40`

Expected: All tests pass

**Step 2: Verify git log**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && git log --oneline -10`

Expected: See the Phase 1 commits in order

**Step 3: Push**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && git push`

---

## Summary of Changes

| File | What changed |
|---|---|
| `muxplex/settings.py` | Added `host`, `port`, `auth`, `session_ttl` to `DEFAULT_SETTINGS` |
| `muxplex/cli.py` | `serve()` reads settings.json with CLI flag overrides; `_add_serve_flags()` helper; argparse defaults are `None`; `upgrade`/`update` consolidated via alias; `install-service` prints deprecation warning; `doctor()` shows serve config |
| `muxplex/tests/test_settings.py` | Tests for new default keys, backward compat with old files, patchability |
| `muxplex/tests/test_cli.py` | Tests for config-driven serve, override precedence, None defaults, deprecation warning, doctor config display; updated existing tests for new None-default signature |
| `README.md` | Updated CLI usage section to document config-driven serve |

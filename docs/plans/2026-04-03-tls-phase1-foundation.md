# TLS Setup — Phase 1: Foundation

> **Execution:** Use the subagent-driven-development workflow to implement this plan.
>
> **Phase 1 of 2.** Complete this phase before starting Phase 2 (auto-detection + Tailscale + mkcert).
>
> **Design doc:** `docs/plans/2026-04-03-tls-setup-design.md`

**Goal:** Add HTTPS support to muxplex so the browser Clipboard API works on non-localhost devices, starting with settings, SSL-enabled `serve()`, self-signed cert generation, and doctor integration.

**Architecture:** Two new settings keys (`tls_cert`, `tls_key`) flow through the existing `serve()` resolution chain (CLI flag → settings.json → default). A new `muxplex/tls.py` module owns all cert logic. When both paths are set and files exist, uvicorn starts with SSL. A `setup-tls` subcommand with self-signed fallback is the v1 entry point.

**Tech Stack:** Python stdlib (`ssl`, `subprocess`, `datetime`), `cryptography` library for self-signed cert generation, uvicorn built-in SSL, argparse.

**Scope boundaries:**
- **IN this phase:** Settings, serve SSL, `--tls-cert`/`--tls-key` flags, self-signed cert generation, `setup-tls` skeleton with `--method selfsigned`, doctor TLS section, tests, README
- **DEFERRED to Phase 2:** Tailscale detection, mkcert detection, auto-detection chain, `--status`, existing cert detection + regenerate prompt
- **OUT of scope entirely:** Automatic cert renewal cron, Caddy integration, Let's Encrypt DNS-01

---

### Task 1: Add `tls_cert` and `tls_key` to DEFAULT_SETTINGS

**Files:**
- Modify: `muxplex/settings.py` (line 16–32, the `DEFAULT_SETTINGS` dict)
- Modify: `muxplex/tests/test_settings.py` (append new tests at bottom)

**Step 1: Write failing tests**

Append to `muxplex/tests/test_settings.py`:

```python
# ============================================================
# TLS settings keys (task: tls-setup phase 1)
# ============================================================


def test_defaults_include_tls_cert():
    """DEFAULT_SETTINGS must have 'tls_cert' key initialised to empty string."""
    assert "tls_cert" in DEFAULT_SETTINGS, (
        "DEFAULT_SETTINGS must include 'tls_cert'"
    )
    assert DEFAULT_SETTINGS["tls_cert"] == "", (
        f"tls_cert default must be '', got: {DEFAULT_SETTINGS['tls_cert']!r}"
    )


def test_defaults_include_tls_key():
    """DEFAULT_SETTINGS must have 'tls_key' key initialised to empty string."""
    assert "tls_key" in DEFAULT_SETTINGS, (
        "DEFAULT_SETTINGS must include 'tls_key'"
    )
    assert DEFAULT_SETTINGS["tls_key"] == "", (
        f"tls_key default must be '', got: {DEFAULT_SETTINGS['tls_key']!r}"
    )


def test_load_returns_tls_keys_when_file_missing():
    """load_settings() returns tls_cert and tls_key with empty defaults when file is missing."""
    result = load_settings()
    assert result["tls_cert"] == "", (
        f"load_settings() tls_cert must default to '', got: {result['tls_cert']!r}"
    )
    assert result["tls_key"] == "", (
        f"load_settings() tls_key must default to '', got: {result['tls_key']!r}"
    )


def test_tls_keys_patchable():
    """patch_settings() must accept and persist tls_cert and tls_key."""
    result = patch_settings({"tls_cert": "/path/to/cert.pem", "tls_key": "/path/to/key.pem"})
    assert result["tls_cert"] == "/path/to/cert.pem", (
        f"patch_settings() must accept tls_cert, got: {result['tls_cert']!r}"
    )
    assert result["tls_key"] == "/path/to/key.pem", (
        f"patch_settings() must accept tls_key, got: {result['tls_key']!r}"
    )
    loaded = load_settings()
    assert loaded["tls_cert"] == "/path/to/cert.pem"
    assert loaded["tls_key"] == "/path/to/key.pem"


def test_old_settings_file_without_tls_keys_loads_correctly(redirect_settings_path):
    """Old settings.json without TLS keys loads correctly with empty defaults filled in."""
    old_settings = {"host": "0.0.0.0", "port": 8088}
    redirect_settings_path.write_text(json.dumps(old_settings))

    result = load_settings()

    assert result["host"] == "0.0.0.0"
    assert result["tls_cert"] == "", (
        f"tls_cert must default to '' for old settings files, got: {result['tls_cert']!r}"
    )
    assert result["tls_key"] == "", (
        f"tls_key must default to '' for old settings files, got: {result['tls_key']!r}"
    )
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_settings.py::test_defaults_include_tls_cert muxplex/tests/test_settings.py::test_defaults_include_tls_key muxplex/tests/test_settings.py::test_load_returns_tls_keys_when_file_missing muxplex/tests/test_settings.py::test_tls_keys_patchable muxplex/tests/test_settings.py::test_old_settings_file_without_tls_keys_loads_correctly -v
```

Expected: FAIL — `"tls_cert" in DEFAULT_SETTINGS` is False.

**Step 3: Add the settings keys**

In `muxplex/settings.py`, add these two keys to the `DEFAULT_SETTINGS` dict, right after the `"federation_key": ""` line (line 31):

```python
    "tls_cert": "",
    "tls_key": "",
```

The dict should end like:
```python
    "federation_key": "",
    "tls_cert": "",
    "tls_key": "",
}
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_settings.py -v
```

Expected: ALL PASS (both new and existing tests).

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/settings.py muxplex/tests/test_settings.py && git commit -m "feat: add tls_cert and tls_key to DEFAULT_SETTINGS"
```

---

### Task 2: Update `serve()` to pass SSL params to uvicorn

**Files:**
- Modify: `muxplex/cli.py` (the `serve()` function, lines 202–234)
- Modify: `muxplex/tests/test_cli.py` (append new tests)

**Step 1: Write failing tests**

Append to `muxplex/tests/test_cli.py`:

```python
# ---------------------------------------------------------------------------
# TLS serve() integration tests
# ---------------------------------------------------------------------------


def test_serve_passes_ssl_params_to_uvicorn(tmp_path, monkeypatch):
    """serve() must pass ssl_certfile and ssl_keyfile to uvicorn.run() when both TLS paths are set and files exist."""
    # Create fake cert and key files
    cert_file = tmp_path / "cert.pem"
    key_file = tmp_path / "key.pem"
    cert_file.write_text("FAKE CERT")
    key_file.write_text("FAKE KEY")

    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({
        "tls_cert": str(cert_file),
        "tls_key": str(key_file),
    }))
    monkeypatch.setattr("muxplex.settings.SETTINGS_PATH", settings_file)

    calls = []

    def fake_run(*args, **kwargs):
        calls.append(kwargs)

    with patch("uvicorn.run", fake_run):
        with patch.dict("sys.modules", {"muxplex.main": MagicMock()}):
            from muxplex.cli import serve
            serve()

    assert len(calls) == 1
    assert calls[0]["ssl_certfile"] == str(cert_file)
    assert calls[0]["ssl_keyfile"] == str(key_file)


def test_serve_no_ssl_when_tls_paths_empty(tmp_path, monkeypatch):
    """serve() must NOT pass ssl_certfile/ssl_keyfile when TLS paths are empty (default)."""
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({}))
    monkeypatch.setattr("muxplex.settings.SETTINGS_PATH", settings_file)

    calls = []

    def fake_run(*args, **kwargs):
        calls.append(kwargs)

    with patch("uvicorn.run", fake_run):
        with patch.dict("sys.modules", {"muxplex.main": MagicMock()}):
            from muxplex.cli import serve
            serve()

    assert len(calls) == 1
    assert "ssl_certfile" not in calls[0]
    assert "ssl_keyfile" not in calls[0]


def test_serve_falls_back_to_http_when_cert_file_missing(tmp_path, monkeypatch, capsys):
    """serve() must warn and skip SSL when cert file in settings doesn't exist on disk."""
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({
        "tls_cert": "/nonexistent/cert.pem",
        "tls_key": "/nonexistent/key.pem",
    }))
    monkeypatch.setattr("muxplex.settings.SETTINGS_PATH", settings_file)

    calls = []

    def fake_run(*args, **kwargs):
        calls.append(kwargs)

    with patch("uvicorn.run", fake_run):
        with patch.dict("sys.modules", {"muxplex.main": MagicMock()}):
            from muxplex.cli import serve
            serve()

    assert len(calls) == 1
    assert "ssl_certfile" not in calls[0]

    captured = capsys.readouterr()
    assert "falling back" in captured.out.lower() or "not found" in captured.out.lower()


def test_serve_prints_https_url_when_tls_active(tmp_path, monkeypatch, capsys):
    """serve() must print https:// URL when TLS is active."""
    cert_file = tmp_path / "cert.pem"
    key_file = tmp_path / "key.pem"
    cert_file.write_text("FAKE CERT")
    key_file.write_text("FAKE KEY")

    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({
        "tls_cert": str(cert_file),
        "tls_key": str(key_file),
    }))
    monkeypatch.setattr("muxplex.settings.SETTINGS_PATH", settings_file)

    with patch("uvicorn.run"):
        with patch.dict("sys.modules", {"muxplex.main": MagicMock()}):
            from muxplex.cli import serve
            serve()

    captured = capsys.readouterr()
    assert "https://" in captured.out


def test_serve_prints_http_url_when_no_tls(tmp_path, monkeypatch, capsys):
    """serve() must print http:// URL when no TLS configured."""
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({}))
    monkeypatch.setattr("muxplex.settings.SETTINGS_PATH", settings_file)

    with patch("uvicorn.run"):
        with patch.dict("sys.modules", {"muxplex.main": MagicMock()}):
            from muxplex.cli import serve
            serve()

    captured = capsys.readouterr()
    assert "http://" in captured.out
    assert "https://" not in captured.out
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_cli.py::test_serve_passes_ssl_params_to_uvicorn muxplex/tests/test_cli.py::test_serve_no_ssl_when_tls_paths_empty -v
```

Expected: FAIL — `serve()` doesn't read `tls_cert`/`tls_key` from settings yet.

**Step 3: Update `serve()` in `muxplex/cli.py`**

Replace the `serve()` function (lines 202–234) with:

```python
def serve(
    host: str | None = None,
    port: int | None = None,
    auth: str | None = None,
    session_ttl: int | None = None,
    tls_cert: str | None = None,
    tls_key: str | None = None,
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
    tls_cert = tls_cert if tls_cert is not None else settings.get("tls_cert", "")
    tls_key = tls_key if tls_key is not None else settings.get("tls_key", "")

    os.environ["MUXPLEX_PORT"] = str(port)
    os.environ["MUXPLEX_AUTH"] = auth
    os.environ["MUXPLEX_SESSION_TTL"] = str(session_ttl)

    # Prevent crash-loop on restart: kill any stale process holding the port
    _kill_stale_port_holder(port)

    from muxplex.main import app  # noqa: PLC0415

    # Resolve TLS: both paths must be non-empty and exist on disk
    ssl_kwargs: dict = {}
    if tls_cert and tls_key:
        from pathlib import Path  # noqa: PLC0415

        cert_exists = Path(tls_cert).is_file()
        key_exists = Path(tls_key).is_file()
        if cert_exists and key_exists:
            ssl_kwargs["ssl_certfile"] = tls_cert
            ssl_kwargs["ssl_keyfile"] = tls_key
        else:
            missing = []
            if not cert_exists:
                missing.append(f"cert ({tls_cert})")
            if not key_exists:
                missing.append(f"key ({tls_key})")
            print(
                f"  Warning: TLS {' and '.join(missing)} not found, falling back to HTTP"
            )

    scheme = "https" if ssl_kwargs else "http"
    print(f"  muxplex → {scheme}://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info", **ssl_kwargs)
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_cli.py -v
```

Expected: ALL PASS (both new and existing tests).

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/cli.py muxplex/tests/test_cli.py && git commit -m "feat: serve() passes SSL params to uvicorn when TLS configured"
```

---

### Task 3: Add `--tls-cert` and `--tls-key` CLI flags

**Files:**
- Modify: `muxplex/cli.py` (the `_add_serve_flags()` function at line 668, and `main()` dispatch at line 813–817)
- Modify: `muxplex/tests/test_cli.py` (append new tests)

**Step 1: Write failing tests**

Append to `muxplex/tests/test_cli.py`:

```python
# ---------------------------------------------------------------------------
# TLS CLI flags tests
# ---------------------------------------------------------------------------


def test_main_passes_tls_cert_and_key_flags():
    """main() with --tls-cert/--tls-key must forward them to serve()."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex", "--tls-cert", "/path/cert.pem", "--tls-key", "/path/key.pem"]):
            main()
        mock_serve.assert_called_once_with(
            host=None, port=None, auth=None, session_ttl=None,
            tls_cert="/path/cert.pem", tls_key="/path/key.pem",
        )


def test_main_passes_none_for_unset_tls_flags():
    """main() with no TLS flags passes None for tls_cert/tls_key to serve()."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex"]):
            main()
        _, kwargs = mock_serve.call_args
        assert kwargs["tls_cert"] is None
        assert kwargs["tls_key"] is None


def test_serve_subcommand_accepts_tls_flags():
    """'muxplex serve --tls-cert ... --tls-key ...' forwards values to serve()."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex", "serve", "--tls-cert", "/c.pem", "--tls-key", "/k.pem"]):
            main()
        _, kwargs = mock_serve.call_args
        assert kwargs["tls_cert"] == "/c.pem"
        assert kwargs["tls_key"] == "/k.pem"
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_cli.py::test_main_passes_tls_cert_and_key_flags muxplex/tests/test_cli.py::test_main_passes_none_for_unset_tls_flags -v
```

Expected: FAIL — `serve()` got unexpected keyword argument `tls_cert` (or argparse doesn't know `--tls-cert`).

**Step 3: Add the CLI flags**

In `muxplex/cli.py`, add these two arguments to the `_add_serve_flags()` function (after the `--session-ttl` argument, before the closing of the function):

```python
    parser.add_argument(
        "--tls-cert",
        default=None,
        dest="tls_cert",
        help="Path to TLS certificate file (default: from settings.json)",
    )
    parser.add_argument(
        "--tls-key",
        default=None,
        dest="tls_key",
        help="Path to TLS private key file (default: from settings.json)",
    )
```

Then update the `main()` dispatch in the `else` block (the default serve path, around line 813–817) to pass the new flags:

```python
    else:
        _check_dependencies()
        serve(
            host=args.host, port=args.port, auth=args.auth, session_ttl=args.session_ttl,
            tls_cert=args.tls_cert, tls_key=args.tls_key,
        )
```

Also update the `elif args.command == "serve"` dispatch path. Currently there isn't one — it falls through to the `else` block. The `else` handles both bare `muxplex` and `muxplex serve`. Both paths need the new kwargs. Since the `else` block handles both cases, this one change is sufficient.

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_cli.py -v
```

Expected: ALL PASS.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/cli.py muxplex/tests/test_cli.py && git commit -m "feat: add --tls-cert and --tls-key CLI flags to serve"
```

---

### Task 4: Create `muxplex/tls.py` with self-signed cert generation

**Files:**
- Create: `muxplex/tls.py`
- Create: `muxplex/tests/test_tls.py`

**Step 1: Write failing tests**

Create `muxplex/tests/test_tls.py`:

```python
"""Tests for muxplex/tls.py — TLS certificate management."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


def test_tls_module_importable():
    """muxplex.tls must be importable."""
    from muxplex.tls import generate_self_signed  # noqa: F401


def test_generate_self_signed_creates_cert_and_key(tmp_path):
    """generate_self_signed() creates cert.pem and key.pem at the specified paths."""
    from muxplex.tls import generate_self_signed

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"

    generate_self_signed(cert_path=cert_path, key_path=key_path)

    assert cert_path.exists(), "cert.pem must be created"
    assert key_path.exists(), "key.pem must be created"


def test_generate_self_signed_cert_is_valid_pem(tmp_path):
    """Generated cert must start with -----BEGIN CERTIFICATE-----."""
    from muxplex.tls import generate_self_signed

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"

    generate_self_signed(cert_path=cert_path, key_path=key_path)

    cert_content = cert_path.read_text()
    assert cert_content.startswith("-----BEGIN CERTIFICATE-----"), (
        f"cert must be PEM format, got: {cert_content[:50]!r}"
    )


def test_generate_self_signed_key_is_valid_pem(tmp_path):
    """Generated key must start with -----BEGIN."""
    from muxplex.tls import generate_self_signed

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"

    generate_self_signed(cert_path=cert_path, key_path=key_path)

    key_content = key_path.read_text()
    assert key_content.startswith("-----BEGIN"), (
        f"key must be PEM format, got: {key_content[:50]!r}"
    )


def test_generate_self_signed_key_permissions(tmp_path):
    """Generated key file must have 0o600 permissions."""
    import stat
    from muxplex.tls import generate_self_signed

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"

    generate_self_signed(cert_path=cert_path, key_path=key_path)

    file_mode = stat.S_IMODE(key_path.stat().st_mode)
    assert file_mode == 0o600, f"key.pem must be 0o600, got {oct(file_mode)}"


def test_generate_self_signed_returns_metadata(tmp_path):
    """generate_self_signed() returns dict with method, cert_path, key_path, hostnames, expires."""
    from muxplex.tls import generate_self_signed

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"

    result = generate_self_signed(cert_path=cert_path, key_path=key_path)

    assert result["method"] == "selfsigned"
    assert result["cert_path"] == str(cert_path)
    assert result["key_path"] == str(key_path)
    assert isinstance(result["hostnames"], list)
    assert len(result["hostnames"]) > 0
    assert "expires" in result


def test_generate_self_signed_creates_parent_dirs(tmp_path):
    """generate_self_signed() creates parent directories if they don't exist."""
    from muxplex.tls import generate_self_signed

    cert_path = tmp_path / "a" / "b" / "cert.pem"
    key_path = tmp_path / "a" / "b" / "key.pem"

    generate_self_signed(cert_path=cert_path, key_path=key_path)

    assert cert_path.exists()
    assert key_path.exists()


def test_get_cert_info_returns_expiry(tmp_path):
    """get_cert_info() returns dict with expires, hostnames, method for a valid cert."""
    from muxplex.tls import generate_self_signed, get_cert_info

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    generate_self_signed(cert_path=cert_path, key_path=key_path)

    info = get_cert_info(cert_path)

    assert "expires" in info
    assert "hostnames" in info
    assert isinstance(info["hostnames"], list)


def test_get_cert_info_returns_none_for_missing_file(tmp_path):
    """get_cert_info() returns None when cert file doesn't exist."""
    from muxplex.tls import get_cert_info

    info = get_cert_info(tmp_path / "nonexistent.pem")
    assert info is None
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_tls.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'muxplex.tls'`.

**Step 3: Create `muxplex/tls.py`**

Create `muxplex/tls.py`:

```python
"""muxplex/tls.py — TLS certificate management.

Handles certificate generation (self-signed, mkcert, Tailscale),
cert inspection (expiry, SANs), and auto-detection of available methods.
"""

import socket
import ssl
from datetime import datetime, timezone
from pathlib import Path


def generate_self_signed(
    cert_path: Path | str,
    key_path: Path | str,
    hostnames: list[str] | None = None,
    days_valid: int = 3650,
) -> dict:
    """Generate a self-signed TLS certificate and private key.

    Args:
        cert_path: Where to write the certificate PEM file.
        key_path: Where to write the private key PEM file.
        hostnames: Subject Alternative Names. Defaults to hostname, localhost, 127.0.0.1, ::1.
        days_valid: Certificate validity in days (default: 10 years).

    Returns:
        Dict with keys: method, cert_path, key_path, hostnames, expires.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import ipaddress

    cert_path = Path(cert_path)
    key_path = Path(key_path)

    if hostnames is None:
        hostname = socket.gethostname()
        hostnames = [hostname, f"{hostname}.local", "localhost"]

    # Generate RSA key
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # Build certificate
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, hostnames[0]),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "muxplex"),
    ])

    now = datetime.now(timezone.utc)
    expires = now.replace(year=now.year + (days_valid // 365))

    # Build SAN list — DNS names and IP addresses
    san_entries: list[x509.GeneralName] = []
    ip_strings = ["127.0.0.1", "::1"]
    for name in hostnames:
        san_entries.append(x509.DNSName(name))
    for ip_str in ip_strings:
        san_entries.append(x509.IPAddress(ipaddress.ip_address(ip_str)))

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(expires)
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .sign(private_key, hashes.SHA256())
    )

    # Write files
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)

    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)

    return {
        "method": "selfsigned",
        "cert_path": str(cert_path),
        "key_path": str(key_path),
        "hostnames": hostnames,
        "expires": expires.isoformat(),
    }


def get_cert_info(cert_path: Path | str) -> dict | None:
    """Inspect a PEM certificate and return metadata.

    Returns:
        Dict with keys: expires, hostnames, not_before, serial.
        Returns None if the file does not exist or is unreadable.
    """
    from cryptography import x509

    cert_path = Path(cert_path)
    if not cert_path.is_file():
        return None

    try:
        cert_data = cert_path.read_bytes()
        cert = x509.load_pem_x509_certificate(cert_data)
    except Exception:
        return None

    # Extract SANs
    hostnames: list[str] = []
    try:
        san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        hostnames.extend(san_ext.value.get_values_for_type(x509.DNSName))
        hostnames.extend(
            str(ip) for ip in san_ext.value.get_values_for_type(x509.IPAddress)
        )
    except x509.ExtensionNotFound:
        pass

    return {
        "expires": cert.not_valid_after_utc.isoformat(),
        "not_before": cert.not_valid_before_utc.isoformat(),
        "hostnames": hostnames,
        "serial": str(cert.serial_number),
    }
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_tls.py -v
```

Expected: ALL PASS. If `cryptography` is not installed, run: `cd muxplex && uv pip install cryptography`

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/tls.py muxplex/tests/test_tls.py && git commit -m "feat: add tls.py with self-signed cert generation and cert inspection"
```

---

### Task 5: Add `setup-tls` subcommand with self-signed path

**Files:**
- Modify: `muxplex/cli.py` (add subcommand registration and dispatch)
- Modify: `muxplex/tests/test_cli.py` (append new tests)

**Step 1: Write failing tests**

Append to `muxplex/tests/test_cli.py`:

```python
# ---------------------------------------------------------------------------
# setup-tls subcommand tests
# ---------------------------------------------------------------------------


def test_setup_tls_subcommand_registered():
    """setup-tls must be a valid subcommand in main() argparse."""
    import io
    from muxplex.cli import main

    buf = io.StringIO()
    with patch("sys.argv", ["muxplex", "--help"]):
        try:
            with patch("sys.stdout", buf):
                main()
        except SystemExit:
            pass

    help_text = buf.getvalue().lower()
    assert "setup-tls" in help_text


def test_main_dispatches_to_setup_tls(monkeypatch):
    """main() with 'setup-tls' subcommand must invoke setup_tls()."""
    import muxplex.cli as cli_mod

    calls = []
    monkeypatch.setattr(cli_mod, "setup_tls", lambda method: calls.append(method))

    with patch("sys.argv", ["muxplex", "setup-tls"]):
        cli_mod.main()

    assert len(calls) == 1


def test_setup_tls_selfsigned_creates_certs(tmp_path, monkeypatch, capsys):
    """setup_tls(method='selfsigned') generates certs and updates settings."""
    import muxplex.settings as settings_mod

    # Redirect settings and config dir to tmp_path
    fake_config = tmp_path / ".config" / "muxplex"
    fake_config.mkdir(parents=True)
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_config / "settings.json")

    from muxplex.cli import setup_tls

    # Monkeypatch the cert/key default paths to use tmp_path
    monkeypatch.setattr("muxplex.settings.SETTINGS_PATH", fake_config / "settings.json")

    setup_tls(method="selfsigned")

    # Settings must have been updated with cert paths
    settings = settings_mod.load_settings()
    assert settings["tls_cert"] != "", "tls_cert must be set after setup-tls"
    assert settings["tls_key"] != "", "tls_key must be set after setup-tls"

    # Cert files must exist
    assert Path(settings["tls_cert"]).exists(), "cert file must exist"
    assert Path(settings["tls_key"]).exists(), "key file must exist"

    # Output must mention method and restart hint
    captured = capsys.readouterr()
    assert "self-signed" in captured.out.lower() or "selfsigned" in captured.out.lower()
    assert "restart" in captured.out.lower()
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_cli.py::test_setup_tls_subcommand_registered muxplex/tests/test_cli.py::test_main_dispatches_to_setup_tls -v
```

Expected: FAIL — `setup-tls` not in help, `setup_tls` not in cli_mod.

**Step 3: Add the subcommand**

In `muxplex/cli.py`, add the `setup_tls()` function before the `main()` function:

```python
def setup_tls(method: str = "auto") -> None:
    """Set up TLS certificates for HTTPS.

    In Phase 1, only 'selfsigned' and 'auto' (which falls through to selfsigned) are supported.
    Phase 2 adds Tailscale and mkcert detection.
    """
    from muxplex.settings import SETTINGS_PATH, patch_settings  # noqa: PLC0415
    from muxplex.tls import generate_self_signed  # noqa: PLC0415

    config_dir = SETTINGS_PATH.parent
    cert_path = config_dir / "cert.pem"
    key_path = config_dir / "key.pem"

    if method in ("auto", "selfsigned"):
        result = generate_self_signed(cert_path=cert_path, key_path=key_path)
        patch_settings({"tls_cert": result["cert_path"], "tls_key": result["key_path"]})

        print(f"\n  TLS configured (self-signed)")
        print(f"  Certificate: {result['cert_path']}")
        print(f"  Key:         {result['key_path']}")
        print(f"  Hostnames:   {', '.join(result['hostnames'])}")
        print(f"  Expires:     {result['expires']}")
        print()
        print("  Warning: Browsers will show a security warning.")
        print("  For trusted certs, install mkcert or use Tailscale.")
        print()
        print("  Restart service to apply: muxplex service restart")
        print()
    else:
        print(f"  Unknown TLS method: {method}", file=sys.stderr)
        print("  Valid methods: auto, selfsigned", file=sys.stderr)
        sys.exit(1)
```

In the `main()` function, register the subparser (add after the `config_parser` block, before `args = parser.parse_args()`):

```python
    setup_tls_parser = sub.add_parser("setup-tls", help="Set up TLS certificates for HTTPS")
    setup_tls_parser.add_argument(
        "--method",
        choices=["auto", "selfsigned"],
        default="auto",
        help="TLS method (default: auto — detects best available)",
    )
```

In the `main()` dispatch section, add a new `elif` before the `else` block:

```python
    elif args.command == "setup-tls":
        setup_tls(method=args.method)
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_cli.py -v
```

Expected: ALL PASS.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/cli.py muxplex/tests/test_cli.py && git commit -m "feat: add setup-tls subcommand with self-signed cert generation"
```

---

### Task 6: Doctor TLS status display

**Files:**
- Modify: `muxplex/cli.py` (the `doctor()` function, insert TLS section)
- Modify: `muxplex/tests/test_cli.py` (append new tests)

**Step 1: Write failing tests**

Append to `muxplex/tests/test_cli.py`:

```python
# ---------------------------------------------------------------------------
# doctor TLS status tests
# ---------------------------------------------------------------------------


def test_doctor_shows_tls_disabled(tmp_path, monkeypatch, capsys):
    """doctor() must show 'TLS: disabled' when no TLS configured."""
    import muxplex.settings as settings_mod

    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({}))
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    from muxplex.cli import doctor
    doctor()

    out = capsys.readouterr().out.lower()
    assert "tls" in out
    assert "disabled" in out


def test_doctor_shows_tls_enabled(tmp_path, monkeypatch, capsys):
    """doctor() must show 'TLS: enabled' with expiry when TLS is configured and certs exist."""
    import muxplex.settings as settings_mod

    cert_file = tmp_path / "cert.pem"
    key_file = tmp_path / "key.pem"

    # Generate real certs
    from muxplex.tls import generate_self_signed
    generate_self_signed(cert_path=cert_file, key_path=key_file)

    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({
        "tls_cert": str(cert_file),
        "tls_key": str(key_file),
    }))
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    from muxplex.cli import doctor
    doctor()

    out = capsys.readouterr().out.lower()
    assert "tls" in out
    assert "enabled" in out


def test_doctor_shows_tls_clipboard_warning(tmp_path, monkeypatch, capsys):
    """doctor() must mention clipboard requires HTTPS when TLS is disabled."""
    import muxplex.settings as settings_mod

    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({}))
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    from muxplex.cli import doctor
    doctor()

    out = capsys.readouterr().out.lower()
    assert "clipboard" in out or "https" in out
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_cli.py::test_doctor_shows_tls_disabled muxplex/tests/test_cli.py::test_doctor_shows_tls_enabled -v
```

Expected: FAIL — doctor output doesn't mention TLS.

**Step 3: Add TLS section to `doctor()`**

In `muxplex/cli.py`, in the `doctor()` function, add the following TLS block right after the "Serve config" section (after line 327 `f" (auth={cfg['auth']}, ttl={cfg['session_ttl']}s)"`) and before the "Auth status" section (line 330 `pw_path = get_password_path()`):

```python
    # TLS status
    tls_cert = cfg.get("tls_cert", "")
    tls_key = cfg.get("tls_key", "")
    if tls_cert and tls_key:
        from muxplex.tls import get_cert_info  # noqa: PLC0415

        cert_info = get_cert_info(tls_cert)
        if cert_info:
            from datetime import datetime, timezone  # noqa: PLC0415

            expires = datetime.fromisoformat(cert_info["expires"])
            now = datetime.now(timezone.utc)
            if expires < now:
                days_ago = (now - expires).days
                print(
                    f"  {warn_mark} TLS: WARNING — cert expired {days_ago} days ago."
                    " Run muxplex setup-tls to renew"
                )
            else:
                print(
                    f"  {ok_mark} TLS: enabled (cert expires {expires.strftime('%Y-%m-%d')})"
                )
        else:
            print(
                f"  {warn_mark} TLS: configured but cert not readable ({tls_cert})"
            )
    else:
        print(
            f"  {warn_mark} TLS: disabled (clipboard requires HTTPS on non-localhost)"
        )
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_cli.py -v
```

Expected: ALL PASS.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/cli.py muxplex/tests/test_cli.py && git commit -m "feat: doctor shows TLS status with expiry and clipboard warning"
```

---

### Task 7: Full test sweep and edge cases

**Files:**
- Modify: `muxplex/tests/test_tls.py` (add edge case tests)
- Modify: `muxplex/tests/test_cli.py` (add edge case tests)

**Step 1: Add edge case tests to `test_tls.py`**

Append to `muxplex/tests/test_tls.py`:

```python
def test_generate_self_signed_with_custom_hostnames(tmp_path):
    """generate_self_signed() accepts custom hostnames list."""
    from muxplex.tls import generate_self_signed

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"

    result = generate_self_signed(
        cert_path=cert_path,
        key_path=key_path,
        hostnames=["mybox.local", "mybox.tailnet.ts.net"],
    )

    assert "mybox.local" in result["hostnames"]
    assert "mybox.tailnet.ts.net" in result["hostnames"]


def test_get_cert_info_hostnames_include_ip(tmp_path):
    """get_cert_info() includes IP SANs from the generated cert."""
    from muxplex.tls import generate_self_signed, get_cert_info

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    generate_self_signed(cert_path=cert_path, key_path=key_path)

    info = get_cert_info(cert_path)

    assert info is not None
    assert "127.0.0.1" in info["hostnames"]


def test_get_cert_info_returns_none_for_corrupt_file(tmp_path):
    """get_cert_info() returns None for a file that isn't valid PEM."""
    from muxplex.tls import get_cert_info

    bad_cert = tmp_path / "bad.pem"
    bad_cert.write_text("THIS IS NOT A CERTIFICATE")

    info = get_cert_info(bad_cert)
    assert info is None
```

**Step 2: Add edge case test to `test_cli.py`**

Append to `muxplex/tests/test_cli.py`:

```python
def test_serve_warns_when_only_cert_set(tmp_path, monkeypatch, capsys):
    """serve() must warn when only tls_cert is set but tls_key is empty."""
    cert_file = tmp_path / "cert.pem"
    cert_file.write_text("FAKE CERT")

    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({
        "tls_cert": str(cert_file),
        "tls_key": "",
    }))
    monkeypatch.setattr("muxplex.settings.SETTINGS_PATH", settings_file)

    calls = []

    def fake_run(*args, **kwargs):
        calls.append(kwargs)

    with patch("uvicorn.run", fake_run):
        with patch.dict("sys.modules", {"muxplex.main": MagicMock()}):
            from muxplex.cli import serve
            serve()

    assert len(calls) == 1
    assert "ssl_certfile" not in calls[0], "SSL must not be enabled with only cert set"
```

**Step 3: Run full test suite**

```bash
cd muxplex && python -m pytest muxplex/tests/test_settings.py muxplex/tests/test_cli.py muxplex/tests/test_tls.py -v
```

Expected: ALL PASS.

**Step 4: Commit**

```bash
cd muxplex && git add muxplex/tests/test_tls.py muxplex/tests/test_cli.py && git commit -m "test: add TLS edge case tests for cert generation and serve fallback"
```

---

### Task 8: README TLS section

**Files:**
- Modify: `README.md`

**Step 1: Add TLS entries to Features section**

In `README.md`, add a new subsection under `### Developer Tools` (after line 56 `- \`muxplex config\` — CLI settings management`):

```markdown

### HTTPS / TLS

- `muxplex setup-tls` — auto-detect and set up TLS certificates
- **Tailscale** — real Let's Encrypt certs via `tailscale cert` (Phase 2)
- **mkcert** — locally-trusted certs (Phase 2)
- **Self-signed** — fallback for immediate HTTPS (browser shows warning)
- Required for browser clipboard API on non-localhost
```

**Step 2: Add TLS row to CLI Reference**

In `README.md`, in the CLI Reference block (around line 127), add after the `muxplex reset-secret` line:

```
muxplex setup-tls [--method auto]   Set up TLS certs for HTTPS
```

**Step 3: Add TLS rows to Configuration table**

In `README.md`, in the Configuration table (around line 179), add after the `multi_device_enabled` row:

```
| `tls_cert` | `""` | Path to TLS certificate file (empty = HTTP) |
| `tls_key` | `""` | Path to TLS private key file (empty = HTTP) |
```

**Step 4: Add TLS Examples section**

In `README.md`, after the Examples section (around line 175), add:

```markdown

### HTTPS / TLS setup

```bash
# Auto-detect best TLS method and configure
muxplex setup-tls

# Force self-signed certificate
muxplex setup-tls --method selfsigned

# Override TLS cert for this run only
muxplex serve --tls-cert /path/cert.pem --tls-key /path/key.pem

# Check TLS status
muxplex doctor
```
```

**Step 5: Commit**

```bash
cd muxplex && git add README.md && git commit -m "docs: add TLS setup section to README"
```

---

## Phase 1 Checklist

After completing all 8 tasks, verify:

```bash
cd muxplex && python -m pytest muxplex/tests/ -v
```

All tests must pass. The following must work:

1. `muxplex setup-tls --method selfsigned` — generates certs, updates settings
2. `muxplex serve` — starts with HTTPS when certs are configured
3. `muxplex serve --tls-cert /path --tls-key /path` — CLI flag override
4. `muxplex doctor` — shows TLS enabled/disabled status
5. `muxplex config get tls_cert` — shows configured cert path

Phase 2 (auto-detection, Tailscale, mkcert) builds on this foundation.